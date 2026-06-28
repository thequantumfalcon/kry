# Build the enclave workload, then package it into a minimal runtime image that
# nitro-cli converts to an EIF. The Rust binary talks to /dev/nsm (provided to the
# enclave by the Nitro hypervisor) and to the parent over vsock — no network needed.
# L7 reproducibility: base images are digest-pinned so the enclave PCR0 measurement is
# reproducible. Refresh a digest with `docker buildx imagetools inspect <image>:<tag>`.
# For a FULLY reproducible PCR0, also commit Cargo.lock (`cargo generate-lockfile`) and add
# `--locked` to the build below — that step needs the Rust toolchain and is the one
# remaining manual item (no committed lockfile yet).
FROM rust:1-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc AS builder
WORKDIR /build
COPY Cargo.toml .
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df
COPY --from=builder /build/target/release/kry-attest-enclave /usr/local/bin/kry-attest-enclave
CMD ["/usr/local/bin/kry-attest-enclave"]
