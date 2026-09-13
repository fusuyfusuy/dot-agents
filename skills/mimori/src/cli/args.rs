use clap::{Args, Parser, Subcommand};

#[derive(Parser, Debug)]
#[command(
    name = "mimori",
    author = "mimori team",
    version,
    about = "High-performance AST code-intelligence and symbol-graph CLI"
)]
pub struct Cli {
    #[arg(long, global = true, help = "Output structured JSON")]
    pub json: bool,

    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    #[command(about = "Initialize .mimori cache directory")]
    Init,

    #[command(about = "Extract token-dense code slice for a symbol or line range")]
    Slice(SliceArgs),

    #[command(about = "Search symbols or files across the repository")]
    Find(FindArgs),

    #[command(about = "Display upstream callers of a target symbol")]
    Up(TargetArgs),

    #[command(about = "Display downstream callees invoked by a target symbol")]
    Down(TargetArgs),

    #[command(about = "List files under a scope whose content lacks a pattern (absence query)")]
    Missing(MissingArgs),

    #[command(about = "Display non-call mentioners of a target symbol (property reads, type refs, arg/template mentions)")]
    Uses(TargetArgs),
    #[command(about = "Evaluate transitive blast radius of a symbol across entry points")]
    Blast(BlastArgs),

    #[command(about = "Generate hierarchical, centrality-ranked architectural map")]
    Map(MapArgs),

    #[command(about = "Purge cached index data in .mimori/")]
    Clean(CleanArgs),

    #[command(about = "Show repository health: stats, hubs, dead-weight candidates")]
    Doctor(DoctorArgs),
}

#[derive(Args, Debug)]
pub struct SliceArgs {
    #[arg(help = "Target coordinate (e.g. 'path/file.rs:symbol' or 'path/file.rs:#L10-50')")]
    pub target: String,

    #[arg(short = 'f', long, help = "Inline private local callee bodies")]
    pub follow_local: bool,

    #[arg(short = 'i', long, help = "Include top-of-file import statements")]
    pub with_imports: bool,

    #[arg(
        long,
        help = "Token budget for markdown output (heuristic chars/token calibrated per language: 3 for Rust/Go, 4 for TS/JS, 5 for Python; core slice is never cut)"
    )]
    pub budget: Option<usize>,
}

#[derive(Args, Debug)]
pub struct FindArgs {
    #[arg(help = "Search pattern")]
    pub pattern: String,

    #[arg(
        short = 's',
        long,
        conflicts_with = "files_only",
        help = "Symbols only"
    )]
    pub symbols_only: bool,

    #[arg(short = 'f', long, help = "Files only")]
    pub files_only: bool,
}

#[derive(Args, Debug)]
pub struct TargetArgs {
    #[arg(help = "Target symbol or coordinate")]
    pub target: String,
}

#[derive(Args, Debug)]
pub struct BlastArgs {
    #[arg(help = "Target symbol or coordinate")]
    pub target: String,

    #[arg(
        short = 'd',
        long,
        default_value_t = 3,
        help = "Maximum traversal depth"
    )]
    pub depth: usize,

    #[arg(long, help = "Traverse downstream callees instead of upstream callers")]
    pub down: bool,

    #[arg(
        long = "with-sinks",
        value_name = "NAMES",
        help = "Comma-separated literal sink substrings to sweep (e.g. 'console.,logPath,sendNotification'). Graph traversal can't see literals; this runs the rg-equivalent sweep in the same call and appends per-line hits."
    )]
    pub with_sinks: Option<String>,
}

#[derive(Args, Debug)]
pub struct MapArgs {
    #[arg(long, help = "Scope directory or module")]
    pub scope: Option<String>,

    #[arg(long, help = "Focus target symbol")]
    pub focus: Option<String>,

    #[arg(long, help = "Seed term for personalized ranking")]
    pub seed: Option<String>,

    #[arg(long, help = "Keep only the top N symbols by centrality")]
    pub limit: Option<usize>,
}

#[derive(Args, Debug)]
pub struct MissingArgs {
    #[arg(help = "Literal substring to look for; `|` separates alternatives (e.g. 'Remote|serverId')")]
    pub pattern: String,

    #[arg(long, help = "Workspace-relative directory to sweep")]
    pub scope: Option<String>,

    #[arg(long, help = "Only consider files whose content contains this literal (e.g. a lane-defining marker)")]
    pub defines: Option<String>,
}

#[derive(Args, Debug)]
pub struct CleanArgs {
    #[arg(long, help = "Purge all caches and memory")]
    pub all: bool,
}

#[derive(Args, Debug)]
pub struct DoctorArgs {
    #[arg(long, help = "Keep only the top N candidates per tier")]
    pub limit: Option<usize>,
}
