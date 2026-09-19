//! Line-delimited JSON transport over the center's Unix domain socket.

use crate::protocol::{Request, Response};
use serde::de::DeserializeOwned;
use serde::Serialize;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[derive(Debug)]
pub struct Channel {
    reader: BufReader<UnixStream>,
    writer: UnixStream,
    path: Option<PathBuf>,
}

impl Channel {
    pub fn new(stream: UnixStream) -> std::io::Result<Self> {
        let writer = stream.try_clone()?;
        Ok(Self {
            reader: BufReader::new(stream),
            writer,
            path: None,
        })
    }

    pub fn connect(path: &Path) -> std::io::Result<Self> {
        let stream = UnixStream::connect(path)?;
        let mut channel = Self::new(stream)?;
        channel.path = Some(path.to_path_buf());
        Ok(channel)
    }

    /// Path this channel was connected to (absent for server-side channels).
    pub fn socket_path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    /// A freshly spawned center may still be binding its socket; retry briefly.
    pub fn connect_with_retry(path: &Path, timeout: Duration) -> std::io::Result<Self> {
        let deadline = Instant::now() + timeout;
        loop {
            match Self::connect(path) {
                Ok(channel) => return Ok(channel),
                Err(error) => {
                    if Instant::now() >= deadline {
                        return Err(error);
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }

    pub fn set_read_timeout(&self, timeout: Option<Duration>) -> std::io::Result<()> {
        self.reader.get_ref().set_read_timeout(timeout)
    }

    pub fn send<T: Serialize>(&mut self, message: &T) -> std::io::Result<()> {
        let mut line = serde_json::to_vec(message).map_err(|error| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
        })?;
        line.push(b'\n');
        self.writer.write_all(&line)?;
        self.writer.flush()
    }

    pub fn recv<T: DeserializeOwned>(&mut self) -> std::io::Result<T> {
        let mut line = String::new();
        let read = self.reader.read_line(&mut line)?;
        if read == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "center closed the connection",
            ));
        }
        serde_json::from_str(line.trim_end()).map_err(|error| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
        })
    }

    /// Send a request and read the matching response (one round trip per call).
    pub fn request(&mut self, request: &Request) -> std::io::Result<Response> {
        self.send(request)?;
        self.recv()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{ClientInfo, Request};

    #[test]
    fn round_trips_over_a_socket_pair() {
        let (server, client) = UnixStream::pair().unwrap();
        let handle = std::thread::spawn(move || {
            let mut channel = Channel::new(server).unwrap();
            let request: Request = channel.recv().unwrap();
            assert_eq!(request, Request::Ping);
            channel
                .send(&Response::ok(serde_json::json!({"pong": true})))
                .unwrap();
        });

        let mut channel = Channel::new(client).unwrap();
        let response = channel.request(&Request::Ping).unwrap();
        let value: serde_json::Value = response.into_result().unwrap();
        assert_eq!(value["pong"], true);
        handle.join().unwrap();
    }

    #[test]
    fn recv_reports_closed_connections() {
        let (server, client) = UnixStream::pair().unwrap();
        drop(server);
        let mut channel = Channel::new(client).unwrap();
        let error = channel.recv::<Request>().unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::UnexpectedEof);
    }

    #[test]
    fn connect_with_retry_times_out_when_nothing_listens() {
        let path = std::path::Path::new("/tmp/sandbox-center-mvp-does-not-exist.sock");
        let _ = std::fs::remove_file(path);
        let error = Channel::connect_with_retry(path, Duration::from_millis(60)).unwrap_err();
        assert!(!error.to_string().is_empty());
    }

    #[test]
    fn round_trips_a_typed_open_session_exchange() {
        use crate::protocol::{OpenSessionRequest, OpenSessionResult};

        let (server, client) = UnixStream::pair().unwrap();
        let handle = std::thread::spawn(move || {
            let mut channel = Channel::new(server).unwrap();
            let request: Request = channel.recv().unwrap();
            let Request::OpenSession(payload) = request else {
                panic!("unexpected request");
            };
            assert_eq!(payload.cwd, "/tmp/ws");
            assert_eq!(payload.client.name, "sandbox-cli");
            channel
                .send(&Response::ok(OpenSessionResult {
                    session_id: "s-1".into(),
                    tag: "SC_SBX_00000001".into(),
                    policy: crate::policy::EffectivePolicy {
                        policy_id: "default@1".into(),
                        read_full: true,
                        read_roots: vec!["/".into()],
                        write_default: crate::policy::Access::Deny,
                        write_roots: vec!["/tmp/ws".into()],
                        delete_default: crate::policy::Access::Deny,
                        delete_roots: vec!["/tmp/ws/.sc-trash".into()],
                        network_mode: crate::policy::NetworkMode::LoopbackOnly,
                        unix_socket_outbound: true,
                        auto_grant: Vec::new(),
                    },
                }))
                .unwrap();
        });

        let mut channel = Channel::new(client).unwrap();
        let response = channel
            .request(&Request::OpenSession(OpenSessionRequest {
                client: ClientInfo {
                    name: "sandbox-cli".into(),
                    pid: 1,
                    version: "0".into(),
                },
                cwd: "/tmp/ws".into(),
                argv: vec!["/bin/zsh".into(), "-c".into(), "ls".into()],
                workspace: None,
                retry: false,
            }))
            .unwrap();
        let result: OpenSessionResult = response.into_result().unwrap();
        assert_eq!(result.session_id, "s-1");
        assert_eq!(result.policy.write_roots, vec!["/tmp/ws".to_string()]);
        handle.join().unwrap();
    }
}
