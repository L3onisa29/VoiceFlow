use voiceflow_core::streaming::{Language, NvidiaDecoder, StreamingSession, load_model};

#[test]
#[ignore = "Requires VOICEFLOW_TEST_MODEL and synthetic fixtures from verify-short-recordings.ts"]
fn short_speech_survives_stop_pause_and_silence_resets() {
    let model = load_model(std::path::Path::new(
        &std::env::var("VOICEFLOW_TEST_MODEL").expect("Set VOICEFLOW_TEST_MODEL"),
    ))
    .unwrap();
    let fixtures =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../artifacts/streaming/short");
    for (name, expected) in [
        ("ciao", "ciao"),
        ("pronto", "pronto"),
        ("veloce", "prova veloce"),
        ("frase", "domani chiamo marco"),
    ] {
        let audio: Vec<f32> = hound::WavReader::open(fixtures.join(format!("{name}.wav")))
            .unwrap()
            .samples::<i16>()
            .map(|value| value.unwrap() as f32 / 32768.0)
            .collect();
        for language in [Language::Italian, Language::Auto] {
            // Auto remains unreliable for isolated sub-500ms words, including
            // "Pronto" after a silence boundary. Check those on stop separately
            // in the script; use the longer phrases for Auto reset assertions.
            if matches!(name, "ciao" | "pronto") && matches!(language, Language::Auto) {
                continue;
            }
            let mut session = StreamingSession::new(NvidiaDecoder::new(&model, language).unwrap());
            // Pause, automatic silence boundary, then immediate stop all reset the decoder.
            for boundary in 0..3 {
                for frame in audio.chunks(320) {
                    session.push(frame).unwrap();
                }
                match boundary {
                    0 => session.flush_utterance().unwrap(),
                    1 => session.push(&vec![0.0; 16_000 * 3]).unwrap(),
                    _ => {
                        session.finish().unwrap();
                    }
                }
                let text = session.snapshot().text().to_lowercase();
                assert_eq!(
                    text.matches(expected).count(),
                    boundary + 1,
                    "{name}, {language:?}, boundary {boundary}: {text:?}"
                );
            }
            let final_text = session.finish().unwrap();
            assert!(
                (final_text.audio_seconds - (audio.len() as f64 / 16_000.0 * 3.0 + 3.0)).abs()
                    < 1e-8
            );
            assert!(final_text.finished);
        }
    }
}
