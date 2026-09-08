use std::io;

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("I/O error while processing a frame: {0}")]
    Io(#[from] io::Error),
    #[error("frame ended during {section}: expected {expected} bytes, received {received}")]
    TruncatedFrame {
        section: &'static str,
        expected: usize,
        received: usize,
    },
    #[error("header length must be greater than zero")]
    EmptyHeader,
    #[error("header length {actual} exceeds the {maximum}-byte limit")]
    HeaderTooLarge { actual: usize, maximum: usize },
    #[error("binary length {actual} exceeds the {maximum}-byte limit")]
    BinaryTooLarge { actual: u64, maximum: u64 },
    #[error("frame header is not valid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("frame header must be a JSON object")]
    HeaderNotObject,
    #[error("frame header must include an unsigned integer binary_len")]
    InvalidBinaryLength,
    #[error("declared binary length {declared} does not match payload length {actual}")]
    BinaryLengthMismatch { declared: u64, actual: usize },
    #[error("frame allocation failed for {section} ({bytes} bytes)")]
    AllocationFailed { section: &'static str, bytes: usize },
    #[error("request header does not match the protocol schema")]
    InvalidRequest,
}
