use std::io::{self, Read, Write};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ProtocolError;

pub const PROTOCOL_MAJOR: u16 = 1;
pub const PROTOCOL_MINOR: u16 = 0;
pub const MAX_HEADER_BYTES: usize = 64 * 1024;
pub const MAX_BINARY_BYTES: u64 = 128 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProtocolVersion {
    pub major: u16,
    pub minor: u16,
}

impl ProtocolVersion {
    #[must_use]
    pub const fn current() -> Self {
        Self {
            major: PROTOCOL_MAJOR,
            minor: PROTOCOL_MINOR,
        }
    }
}

#[derive(Debug)]
pub struct Frame {
    pub header: Value,
    pub binary: Vec<u8>,
}

#[derive(Debug, Deserialize)]
pub struct RequestHeader {
    pub protocol: ProtocolVersion,
    pub id: String,
    pub method: String,
    #[serde(default = "empty_object")]
    pub params: Value,
    pub binary_len: u64,
}

impl RequestHeader {
    pub fn from_value(value: Value) -> Result<Self, ProtocolError> {
        serde_json::from_value(value).map_err(|_| ProtocolError::InvalidRequest)
    }
}

fn empty_object() -> Value {
    Value::Object(serde_json::Map::new())
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub category: String,
}

impl ErrorBody {
    #[must_use]
    pub fn protocol(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_owned(),
            message: message.into(),
            retryable: false,
            category: "protocol".to_owned(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ResponseHeader {
    pub protocol: ProtocolVersion,
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
    pub binary_len: u64,
}

impl ResponseHeader {
    #[must_use]
    pub fn success(id: String, result: Value) -> Self {
        Self {
            protocol: ProtocolVersion::current(),
            id,
            result: Some(result),
            error: None,
            binary_len: 0,
        }
    }

    #[must_use]
    pub fn error(id: String, error: ErrorBody) -> Self {
        Self {
            protocol: ProtocolVersion::current(),
            id,
            result: None,
            error: Some(error),
            binary_len: 0,
        }
    }
}

pub fn read_frame<R: Read>(reader: &mut R) -> Result<Option<Frame>, ProtocolError> {
    let mut prefix = [0_u8; 4];
    if !read_section(reader, &mut prefix, "header length", true)? {
        return Ok(None);
    }

    let header_len = u32::from_be_bytes(prefix) as usize;
    if header_len == 0 {
        return Err(ProtocolError::EmptyHeader);
    }
    if header_len > MAX_HEADER_BYTES {
        return Err(ProtocolError::HeaderTooLarge {
            actual: header_len,
            maximum: MAX_HEADER_BYTES,
        });
    }

    let mut header_bytes = allocate_zeroed("header", header_len)?;
    read_section(reader, &mut header_bytes, "JSON header", false)?;
    let header: Value = serde_json::from_slice(&header_bytes)?;
    let object = header.as_object().ok_or(ProtocolError::HeaderNotObject)?;
    let binary_len = object
        .get("binary_len")
        .and_then(Value::as_u64)
        .ok_or(ProtocolError::InvalidBinaryLength)?;
    if binary_len > MAX_BINARY_BYTES {
        return Err(ProtocolError::BinaryTooLarge {
            actual: binary_len,
            maximum: MAX_BINARY_BYTES,
        });
    }

    let binary_len_usize =
        usize::try_from(binary_len).map_err(|_| ProtocolError::BinaryTooLarge {
            actual: binary_len,
            maximum: MAX_BINARY_BYTES,
        })?;
    let mut binary = allocate_zeroed("binary payload", binary_len_usize)?;
    read_section(reader, &mut binary, "binary payload", false)?;

    Ok(Some(Frame { header, binary }))
}

pub fn write_frame<W: Write, T: Serialize>(
    writer: &mut W,
    header: &T,
    binary: &[u8],
) -> Result<(), ProtocolError> {
    let header_value = serde_json::to_value(header)?;
    let object = header_value
        .as_object()
        .ok_or(ProtocolError::HeaderNotObject)?;
    let declared = object
        .get("binary_len")
        .and_then(Value::as_u64)
        .ok_or(ProtocolError::InvalidBinaryLength)?;
    if declared > MAX_BINARY_BYTES {
        return Err(ProtocolError::BinaryTooLarge {
            actual: declared,
            maximum: MAX_BINARY_BYTES,
        });
    }
    if declared != binary.len() as u64 {
        return Err(ProtocolError::BinaryLengthMismatch {
            declared,
            actual: binary.len(),
        });
    }

    let header_bytes = serde_json::to_vec(&header_value)?;
    if header_bytes.is_empty() {
        return Err(ProtocolError::EmptyHeader);
    }
    if header_bytes.len() > MAX_HEADER_BYTES {
        return Err(ProtocolError::HeaderTooLarge {
            actual: header_bytes.len(),
            maximum: MAX_HEADER_BYTES,
        });
    }

    let header_len =
        u32::try_from(header_bytes.len()).map_err(|_| ProtocolError::HeaderTooLarge {
            actual: header_bytes.len(),
            maximum: MAX_HEADER_BYTES,
        })?;
    writer.write_all(&header_len.to_be_bytes())?;
    writer.write_all(&header_bytes)?;
    writer.write_all(binary)?;
    writer.flush()?;
    Ok(())
}

fn allocate_zeroed(section: &'static str, bytes: usize) -> Result<Vec<u8>, ProtocolError> {
    let mut output = Vec::new();
    output
        .try_reserve_exact(bytes)
        .map_err(|_| ProtocolError::AllocationFailed { section, bytes })?;
    output.resize(bytes, 0);
    Ok(output)
}

fn read_section<R: Read>(
    reader: &mut R,
    buffer: &mut [u8],
    section: &'static str,
    clean_initial_eof: bool,
) -> Result<bool, ProtocolError> {
    let mut received = 0;
    while received < buffer.len() {
        match reader.read(&mut buffer[received..]) {
            Ok(0) if clean_initial_eof && received == 0 => return Ok(false),
            Ok(0) => {
                return Err(ProtocolError::TruncatedFrame {
                    section,
                    expected: buffer.len(),
                    received,
                });
            }
            Ok(count) => received += count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(ProtocolError::Io(error)),
        }
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct OneByteReader<R>(R);

    impl<R: Read> Read for OneByteReader<R> {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let length = buffer.len().min(1);
            self.0.read(&mut buffer[..length])
        }
    }

    #[derive(Default)]
    struct TwoByteWriter(Vec<u8>);

    impl Write for TwoByteWriter {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            let length = buffer.len().min(2);
            self.0.extend_from_slice(&buffer[..length]);
            Ok(length)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn reads_frame_from_short_reads() {
        let header = json!({"binary_len": 3, "kind": "fixture"});
        let mut encoded = Vec::new();
        write_frame(&mut encoded, &header, b"abc").unwrap();

        let mut reader = OneByteReader(std::io::Cursor::new(encoded));
        let frame = read_frame(&mut reader).unwrap().unwrap();
        assert_eq!(frame.header, header);
        assert_eq!(frame.binary, b"abc");
    }

    #[test]
    fn writes_complete_frame_to_short_writer() {
        let header = json!({"binary_len": 0, "kind": "fixture"});
        let mut writer = TwoByteWriter::default();
        write_frame(&mut writer, &header, &[]).unwrap();

        let frame = read_frame(&mut std::io::Cursor::new(writer.0))
            .unwrap()
            .unwrap();
        assert_eq!(frame.header, header);
    }

    #[test]
    fn rejects_declared_length_mismatch() {
        let header = json!({"binary_len": 2});
        let error = write_frame(&mut Vec::new(), &header, b"one").unwrap_err();
        assert!(matches!(
            error,
            ProtocolError::BinaryLengthMismatch {
                declared: 2,
                actual: 3
            }
        ));
    }
}
