# Build the enclave workload, then package it into a minimal runtime image that
# nitro-cli converts to an EIF. The Rust binary talks to /dev/nsm (provided to the
# enclave by the Nitro hypervisor) and to the parent over vsock — no network needed.
FROM rust:1-bookworm AS builder
WORKDIR /build
COPY Cargo.toml .
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /build/target/release/kry-attest-enclave /usr/local/bin/kry-attest-enclave
CMD ["/usr/local/bin/kry-attest-enclave"]
