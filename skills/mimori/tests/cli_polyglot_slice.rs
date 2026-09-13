use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_slice_python_function_and_class() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("auth.py");
    let code = r#"
class AuthManager:
    def __init__(self, secret: str):
        self.secret = secret

    def verify_token(self, token: str) -> bool:
        return token.startswith(self.secret)

async def generate_token(user_id: str) -> str:
    return f"tok_{user_id}"
"#;
    fs::write(&file_path, code).unwrap();

    // Slice Python class
    let target_class = format!("{}:AuthManager", file_path.to_str().unwrap());
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.arg("slice").arg(&target_class);
    cmd1.assert()
        .success()
        .stdout(predicate::str::contains("class AuthManager"))
        .stdout(predicate::str::contains("self.secret = secret"));

    // Slice Python method inside class
    let target_method = format!("{}:verify_token", file_path.to_str().unwrap());
    let mut cmd2 = Command::cargo_bin("mimori").unwrap();
    cmd2.arg("slice").arg(&target_method);
    cmd2.assert()
        .success()
        .stdout(predicate::str::contains(
            "def verify_token(self, token: str) -> bool",
        ))
        .stdout(predicate::str::contains(
            "return token.startswith(self.secret)",
        ));

    // Slice Python async function
    let target_func = format!("{}:generate_token", file_path.to_str().unwrap());
    let mut cmd3 = Command::cargo_bin("mimori").unwrap();
    cmd3.arg("slice").arg(&target_func);
    cmd3.assert().success().stdout(predicate::str::contains(
        "async def generate_token(user_id: str) -> str",
    ));
}

#[test]
fn test_cli_slice_go_struct_interface_and_method() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("server.go");
    let code = r#"package main

import "fmt"

type Handler interface {
    Handle(req string) string
}

type Server struct {
    Port int
}

func NewServer(port int) *Server {
    return &Server{Port: port}
}

func (s *Server) Start() error {
    fmt.Println("Server running")
    return nil
}
"#;
    fs::write(&file_path, code).unwrap();

    // Slice Go interface
    let target_iface = format!("{}:Handler", file_path.to_str().unwrap());
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.arg("slice").arg(&target_iface);
    cmd1.assert()
        .success()
        .stdout(predicate::str::contains("type Handler interface"))
        .stdout(predicate::str::contains("Handle(req string) string"));

    // Slice Go struct
    let target_struct = format!("{}:Server", file_path.to_str().unwrap());
    let mut cmd2 = Command::cargo_bin("mimori").unwrap();
    cmd2.arg("slice").arg(&target_struct);
    cmd2.assert()
        .success()
        .stdout(predicate::str::contains("type Server struct"))
        .stdout(predicate::str::contains("Port int"));

    // Slice Go function
    let target_func = format!("{}:NewServer", file_path.to_str().unwrap());
    let mut cmd3 = Command::cargo_bin("mimori").unwrap();
    cmd3.arg("slice").arg(&target_func);
    cmd3.assert()
        .success()
        .stdout(predicate::str::contains("func NewServer(port int) *Server"));

    // Slice Go method with receiver
    let target_method = format!("{}:Start", file_path.to_str().unwrap());
    let mut cmd4 = Command::cargo_bin("mimori").unwrap();
    cmd4.arg("slice").arg(&target_method);
    cmd4.assert()
        .success()
        .stdout(predicate::str::contains("func (s *Server) Start() error"))
        .stdout(predicate::str::contains("Server running"));
}
