// Isolated file-only feasibility probe. No microphone, playback or network.
#include <nemo_speech/asr.h>
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <vector>

using Clock = std::chrono::steady_clock;
static double seconds(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}
static void check(nemo_speech_asr_status status) {
    if (status != NEMO_SPEECH_ASR_OK)
        throw std::runtime_error(nemo_speech_asr_last_error());
}
static std::string quote(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32)
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        else out << c;
    }
    out << '"';
    return out.str();
}

int main(int argc, char** argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("usage: native-boost MODEL.gguf MANIFEST.tsv (id language boost PCM16.raw phrases-comma-separated)");
        nemo_speech_asr_backend_config backend{};
        backend.size = sizeof(backend);
        backend.gpu = -1;
        nemo_speech_asr_model_config model{};
        model.size = sizeof(model);
        model.path = argv[1];
        nemo_speech_asr_streaming_config streaming{};
        streaming.size = sizeof(streaming);
        streaming.rnnt_right_context = 1;
        streaming.chunk_size = 0.16f;
        streaming.ctc_left_padding = 1.92f;
        streaming.ctc_right_padding = 1.92f;
        nemo_speech_asr_recognizer_config config{};
        config.size = sizeof(config);
        config.backend = &backend;
        config.model = &model;
        config.streaming = &streaming;
        nemo_speech_asr_recognizer* recognizer = nullptr;
        auto start = Clock::now();
        check(nemo_speech_asr_create(&config, &recognizer));
        std::cout << "{\"event\":\"loaded\",\"seconds\":" << seconds(start)
                  << ",\"version\":" << quote(nemo_speech_asr_version()) << "}" << std::endl;

        std::ifstream manifest(argv[2]);
        if (!manifest) throw std::runtime_error("manifest missing");
        std::string line;
        while (std::getline(manifest, line)) {
            std::istringstream row(line);
            std::string id, lang, weight, path, phrases;
            std::getline(row, id, '\t');
            std::getline(row, lang, '\t');
            std::getline(row, weight, '\t');
            std::getline(row, path, '\t');
            std::getline(row, phrases);
            if (id.empty()) continue;
            std::ifstream input(path, std::ios::binary);
            if (!input) throw std::runtime_error("PCM missing: " + path);
            std::vector<float> samples;
            unsigned char bytes[2];
            while (input.read(reinterpret_cast<char*>(bytes), 2)) {
                auto value = static_cast<int16_t>(uint16_t(bytes[0]) | (uint16_t(bytes[1]) << 8));
                samples.push_back(value / 32768.0f);
            }
            if (samples.empty() || input.gcount() != 0)
                throw std::runtime_error("empty or partial PCM");

            std::vector<std::string> terms;
            std::istringstream words(phrases);
            std::string term;
            while (std::getline(words, term, ',')) terms.push_back(term);
            std::vector<const char*> pointers;
            for (auto& item : terms) pointers.push_back(item.c_str());
            nemo_speech_asr_speech_context context{};
            context.size = sizeof(context);
            context.phrases = pointers.data();
            context.phrase_count = pointers.size();
            context.boost = std::stof(weight);
            auto options = nemo_speech_asr_recognition_options_default();
            options.language_code = lang.c_str();
            options.interim_results = true;
            options.enable_automatic_punctuation = true;
            options.verbatim_transcripts = true;
            if (context.boost > 0) {
                options.speech_contexts = &context;
                options.speech_context_count = 1;
            }
            nemo_speech_asr_stream* stream = nullptr;
            check(nemo_speech_asr_streaming_recognize(recognizer, &options, &stream));
            auto begin = Clock::now();
            double first = -1;
            std::string transcript;
            size_t results = 0;
            size_t finals = 0;
            auto drain = [&]() {
                for (;;) {
                    nemo_speech_asr_result* result = nullptr;
                    check(nemo_speech_asr_stream_next(stream, &result));
                    if (!result) break;
                    const char* text = nemo_speech_asr_result_transcript(result, 0);
                    const bool final = nemo_speech_asr_result_is_final(result);
                    if (final) ++finals;
                    if (text && *text) {
                        if (first < 0) first = seconds(begin);
                    }
                    // A final empty transcript supersedes an earlier partial.
                    if (final || (text && *text)) transcript = text ? text : "";
                    ++results;
                    nemo_speech_asr_result_destroy(result);
                }
            };
            for (size_t at = 0; at < samples.size(); at += 320) {
                check(nemo_speech_asr_stream_push_f32(stream, samples.data() + at,
                      std::min(size_t(320), samples.size() - at), 16000));
                drain();
            }
            auto finish = Clock::now();
            check(nemo_speech_asr_stream_finish(stream));
            drain();
            double finalize = seconds(finish);
            double elapsed = seconds(begin);
            nemo_speech_asr_stream_close(stream);
            if (finals != 1) throw std::runtime_error("Expected exactly one final result with endpointing disabled");
            struct rusage usage{};
            getrusage(RUSAGE_SELF, &usage);
            std::cout << "{\"event\":\"result\",\"id\":" << quote(id)
                      << ",\"boost\":" << context.boost
                      << ",\"audioSeconds\":" << samples.size() / 16000.0
                      << ",\"wallSeconds\":" << elapsed
                      << ",\"firstPartialSeconds\":" << first
                      << ",\"finalizeSeconds\":" << finalize
                      << ",\"peakRssBytes\":" << usage.ru_maxrss
                      << ",\"results\":" << results
                      << ",\"finals\":" << finals
                      << ",\"text\":" << quote(transcript) << "}" << std::endl;
        }
        nemo_speech_asr_destroy(recognizer);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
