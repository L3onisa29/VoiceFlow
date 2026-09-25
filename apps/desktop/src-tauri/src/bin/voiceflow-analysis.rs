fn main() {
    if let Err(error) = voiceflow_desktop::scheduler::entry() {
        eprintln!("VoiceFlow scheduler: {error}");
        std::process::exit(1);
    }
}
