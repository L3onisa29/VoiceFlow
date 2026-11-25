//! Local measurement of the model on the voice profile. Reads only; prints numbers.
fn main() {
    if let Err(error) = voiceflow_desktop::voice_profile::evaluation::entry() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
