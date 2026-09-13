pub mod cli;
pub mod graph;
pub mod model;
pub mod parser;
pub mod storage;
pub mod workspace;

pub use cli::*;
pub use graph::*;
pub use model::*;
pub use parser::*;
pub use storage::*;
pub use workspace::*;

/// Phase timer for the indexing pipeline, printed to stderr when
/// `MIMORI_PROFILE` is set. Zero cost otherwise: `Phase::start` returns `None`
/// and nothing is recorded.
///
/// ```text
/// MIMORI_PROFILE=1 mimori map >/dev/null
///   [profile] scan+read+hash    95.2ms
///   [profile] load_all_symbols 553.4ms
///   ...
/// ```
pub struct Phase(&'static str, std::time::Instant);
impl Phase {
    pub fn start(name: &'static str) -> Option<Phase> {
        std::env::var_os("MIMORI_PROFILE").map(|_| Phase(name, std::time::Instant::now()))
    }
}
impl Drop for Phase {
    fn drop(&mut self) {
        eprintln!(
            "  [profile] {:<24} {:>9.1}ms",
            self.0,
            self.1.elapsed().as_secs_f64() * 1000.0
        );
    }
}
