// Independent, off-prover notary (§7a). This runs the SAME TLSNotary
// verifier/notary role as the in-process `notary()` in `prove.rs`, but:
//   * it listens on a TCP socket, so it can live on a DIFFERENT host than the
//     prover (the MPC-TLS session runs over that TCP connection), and
//   * it signs attestations with a REAL key supplied via `NOTARY_KEY_HEX`
//     (a 32-byte hex seed) rather than the demo `[1u8; 32]` dummy, and prints
//     its public verifying key at startup so a verifier can trust the *notary*
//     (whoever holds this key) rather than the operator running the prover.
//
// Everything between `Session::new(..)` and `socket.close()` is a verbatim copy
// of `prove.rs::notary()` so behaviour is identical to the proven in-process
// flow — only the transport and the signing key differ.
use std::env;

use anyhow::Result;
use futures::io::{AsyncReadExt as _, AsyncWriteExt as _};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::TcpListener;
use tokio_util::compat::TokioAsyncReadCompatExt;

use tlsn::{
    Session,
    attestation::{
        Attestation, AttestationConfig, CryptoProvider, request::Request as AttestationRequest,
        signing::Secp256k1Signer,
    },
    config::verifier::VerifierConfig,
    connection::{CertBinding, ConnectionInfo, TranscriptLength},
    transcript::ContentType,
    verifier::{VerifierCommitStart, VerifierOutput},
    webpki::{CertificateDer, RootCertStore},
};
use tlsn_server_fixture_certs::CA_CERT_DER;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();

    let bind = env::var("NOTARY_BIND").unwrap_or_else(|_| "0.0.0.0:7047".to_string());
    let key_hex = env::var("NOTARY_KEY_HEX")
        .expect("set NOTARY_KEY_HEX to a 32-byte hex signing-key seed");
    let key_bytes = hex::decode(key_hex.trim())?;
    let signing_key = k256::ecdsa::SigningKey::from_bytes(key_bytes.as_slice().into())?;
    let pubkey = signing_key.verifying_key().to_encoded_point(true);
    println!(
        "notary public key (k256, compressed): {}",
        hex::encode(pubkey.as_bytes())
    );
    println!("notary listening on {bind}");

    let listener = TcpListener::bind(&bind).await?;
    loop {
        let (socket, peer) = listener.accept().await?;
        println!("prover connected from {peer}");
        let sk = signing_key.clone();
        match notary(socket, sk).await {
            Ok(()) => println!("attestation issued to {peer}"),
            Err(e) => eprintln!("notary session error ({peer}): {e:#}"),
        }
    }
}

async fn notary<S: AsyncWrite + AsyncRead + Send + Sync + Unpin + 'static>(
    socket: S,
    signing_key: k256::ecdsa::SigningKey,
) -> Result<()> {
    // Create a session with the prover.
    let session = Session::new(socket.compat());
    let (driver, mut handle) = session.split();

    // Spawn the session driver to run in the background.
    let driver_task = tokio::spawn(driver);

    // Create a root certificate store with the server-fixture's self-signed
    // certificate. This is only required for offline testing with the
    // server-fixture.
    let verifier_config = VerifierConfig::builder()
        .root_store(RootCertStore {
            roots: vec![CertificateDer(CA_CERT_DER.to_vec())],
        })
        .build()
        .unwrap();

    let verifier = match handle.new_verifier(verifier_config)?.commit().await? {
        VerifierCommitStart::Mpc(verifier) => verifier.accept().await?.run().await?,
        VerifierCommitStart::Proxy(verifier) => {
            verifier.reject(Some("expecting to use MPC-TLS")).await?;
            return Err(anyhow::anyhow!("protocol configuration rejected"));
        }
    };

    let (
        VerifierOutput {
            transcript_commitments,
            ..
        },
        verifier,
    ) = verifier.verify().await?.accept().await?;

    let tls_transcript = verifier.tls_transcript().clone();

    verifier.close().await?;

    let sent_len = tls_transcript
        .sent()
        .iter()
        .filter_map(|record| {
            if let ContentType::ApplicationData = record.typ {
                Some(record.ciphertext.len())
            } else {
                None
            }
        })
        .sum::<usize>();

    let recv_len = tls_transcript
        .recv()
        .iter()
        .filter_map(|record| {
            if let ContentType::ApplicationData = record.typ {
                Some(record.ciphertext.len())
            } else {
                None
            }
        })
        .sum::<usize>();

    // Close the session and wait for the driver to complete, reclaiming the socket.
    handle.close();
    let mut socket = driver_task.await??;

    // Receive attestation request from prover.
    let mut request_bytes = Vec::new();
    socket.read_to_end(&mut request_bytes).await?;
    let request: AttestationRequest = bincode::deserialize(&request_bytes)?;

    // Sign with the notary's real key (NOTARY_KEY_HEX), not the demo dummy.
    let signer = Box::new(Secp256k1Signer::new(&signing_key.to_bytes())?);
    let mut provider = CryptoProvider::default();
    provider.signer.set_signer(signer);

    // Build an attestation.
    let mut att_config_builder = AttestationConfig::builder();
    att_config_builder.supported_signature_algs(Vec::from_iter(provider.signer.supported_algs()));
    let att_config = att_config_builder.build()?;

    let CertBinding::V1_2(binding) = tls_transcript.certificate_binding() else {
        panic!("unsupported cert binding version");
    };
    let mut builder = Attestation::builder(&att_config).accept_request(request)?;
    builder
        .connection_info(ConnectionInfo {
            time: tls_transcript.time(),
            version: tls_transcript.version(),
            transcript_length: TranscriptLength {
                sent: sent_len as u32,
                received: recv_len as u32,
            },
        })
        .server_ephemeral_key(binding.server_ephemeral_key.clone())
        .transcript_commitments(transcript_commitments);

    let attestation = builder.build(&provider)?;

    // Send attestation to prover.
    let attestation_bytes = bincode::serialize(&attestation)?;
    socket.write_all(&attestation_bytes).await?;
    socket.close().await?;

    Ok(())
}
