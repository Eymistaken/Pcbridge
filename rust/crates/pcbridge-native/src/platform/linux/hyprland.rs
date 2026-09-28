//! Bounded read-only IPC against an already selected Hyprland session.

use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

use async_io::{Async, Timer};
use futures_lite::future;
use futures_lite::io::{AsyncReadExt, AsyncWriteExt};
use serde_json::Value;

pub(crate) enum ReadQuery {
    Monitors,
    Locked,
}

pub(crate) fn query(socket: &Path, command: ReadQuery) -> Result<Value, String> {
    let (request, timeout, limit) = match command {
        ReadQuery::Monitors => (
            b"j/monitors".as_slice(),
            Duration::from_millis(200),
            4 * 1024 * 1024,
        ),
        ReadQuery::Locked => (b"j/locked".as_slice(), Duration::from_millis(200), 4096),
    };
    future::block_on(future::or(
        async {
            let mut stream = Async::<UnixStream>::connect(socket)
                .await
                .map_err(|e| e.to_string())?;
            stream.write_all(request).await.map_err(|e| e.to_string())?;
            let mut reply = Vec::new();
            stream
                .take(limit + 1)
                .read_to_end(&mut reply)
                .await
                .map_err(|e| e.to_string())?;
            if reply.len() as u64 > limit {
                return Err("Hyprland reply exceeds the size limit".into());
            }
            serde_json::from_slice(&reply).map_err(|_| "Hyprland returned invalid JSON".into())
        },
        async {
            Timer::after(timeout).await;
            Err("Hyprland IPC deadline exceeded".into())
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::{ReadQuery, query};
    use std::io::{Read, Write};
    use std::os::unix::net::UnixListener;
    use std::time::{Duration, Instant};

    #[test]
    fn incomplete_and_oversized_lock_replies_are_refused() {
        let directory =
            std::env::temp_dir().join(format!("pcbridge-hyprland-ipc-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        for (index, oversized) in [false, true].into_iter().enumerate() {
            let path = directory.join(format!("{index}.sock"));
            let listener = UnixListener::bind(&path).unwrap();
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0; 8];
                stream.read_exact(&mut request).unwrap();
                assert_eq!(&request, b"j/locked");
                if oversized {
                    stream.write_all(&vec![b' '; 4097]).unwrap();
                } else {
                    stream.write_all(b"{\"locked\":").unwrap();
                    std::thread::sleep(Duration::from_millis(400));
                }
            });
            let started = Instant::now();
            let error = query(&path, ReadQuery::Locked).unwrap_err();
            assert!(started.elapsed() < Duration::from_millis(350));
            assert!(
                error.contains(if oversized { "size limit" } else { "deadline" }),
                "{error}"
            );
            server.join().unwrap();
        }
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn a_stalled_monitor_query_has_an_absolute_deadline() {
        let directory =
            std::env::temp_dir().join(format!("pcbridge-monitor-deadline-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("ipc.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 10];
            stream.read_exact(&mut request).unwrap();
            assert_eq!(&request, b"j/monitors");
            stream.write_all(b"[").unwrap();
            std::thread::sleep(Duration::from_millis(400));
        });
        let started = Instant::now();
        assert!(
            query(&path, ReadQuery::Monitors)
                .unwrap_err()
                .contains("deadline")
        );
        assert!(started.elapsed() < Duration::from_millis(350));
        server.join().unwrap();
        std::fs::remove_dir_all(directory).unwrap();
    }
}
