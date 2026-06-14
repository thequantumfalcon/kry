//! KRY tee_attested PoC — enclave side.
//!
//! A minimal Nitro enclave workload: listen on vsock, receive two length-prefixed frames
//! from the parent (user_data = the KRY measurement JSON, then nonce), ask the Nitro
//! Security Module (NSM) for a signed attestation document that carries them, and send the
//! raw COSE_Sign1 document back as one length-prefixed frame. The parent verifies it with
//! scripts/kry_tee_verify.py against the real AWS Nitro root.
//!
//! NSM API verified against aws/aws-nitro-enclaves-nsm-api src/api/mod.rs (Request/Response)
//! and richardfan1126/nitro-enclaves-nsm-cli (call sequence). No network — vsock only.

use std::io::{Read, Write};

use aws_nitro_enclaves_nsm_api::api::{Request, Response};
use aws_nitro_enclaves_nsm_api::driver::{nsm_exit, nsm_init, nsm_process_request};
use serde_bytes::ByteBuf;
use vsock::{VsockAddr, VsockListener, VsockStream, VMADDR_CID_ANY};

const PORT: u32 = 5005;

fn read_frame(s: &mut VsockStream) -> std::io::Result<Vec<u8>> {
    let mut len_buf = [0u8; 4];
    s.read_exact(&mut len_buf)?;
    let len = u32::from_be_bytes(len_buf) as usize;
    let mut buf = vec![0u8; len];
    s.read_exact(&mut buf)?;
    Ok(buf)
}

fn write_frame(s: &mut VsockStream, data: &[u8]) -> std::io::Result<()> {
    s.write_all(&(data.len() as u32).to_be_bytes())?;
    s.write_all(data)?;
    s.flush()
}

fn handle(stream: &mut VsockStream) -> std::io::Result<()> {
    let user_data = read_frame(stream)?;
    let nonce = read_frame(stream)?;

    let fd = nsm_init();
    if fd < 0 {
        panic!("nsm_init failed (is this running inside a Nitro enclave?)");
    }
    let request = Request::Attestation {
        user_data: Some(ByteBuf::from(user_data)),
        nonce: Some(ByteBuf::from(nonce)),
        public_key: None,
    };
    let response = nsm_process_request(fd, request);
    nsm_exit(fd);

    match response {
        Response::Attestation { document } => {
            eprintln!("enclave: attested {} bytes", document.len());
            write_frame(stream, &document)
        }
        _ => {
            eprintln!("enclave: unexpected NSM response");
            write_frame(stream, b"")
        }
    }
}

fn main() {
    let listener = VsockListener::bind(&VsockAddr::new(VMADDR_CID_ANY, PORT))
        .expect("bind vsock listener");
    eprintln!("enclave: listening on vsock port {PORT}");
    loop {
        match listener.accept() {
            Ok((mut stream, addr)) => {
                eprintln!("enclave: connection from {addr:?}");
                if let Err(e) = handle(&mut stream) {
                    eprintln!("enclave: handler error: {e}");
                }
            }
            Err(e) => eprintln!("enclave: accept error: {e}"),
        }
    }
}
