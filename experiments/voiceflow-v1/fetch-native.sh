#!/bin/sh
# Downloads pinned experiment artifacts only. No installer or product changes.
set -eu
cd "$(dirname "$0")/../.."
mkdir -p artifacts/boosting
fetch() {
  url=$1
  destination=$2
  digest=$3
  if test -f "$destination" && printf '%s  %s\n' "$digest" "$destination" | shasum -a 256 -c -; then
    return
  fi
  curl --fail --location --proto '=https' --proto-redir '=https' --retry 2 "$url" -o "$destination.download"
  printf '%s  %s\n' "$digest" "$destination.download" | shasum -a 256 -c -
  mv "$destination.download" "$destination"
}
fetch https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.1.0/nemo-speech-0.1.0-macos-aarch64-cpu.tar.gz \
  artifacts/boosting/runtime.tar.gz 971661d38d4bf97a63c528d13041a964316d25068d8df045e5b4839848092f25
tar -xzf artifacts/boosting/runtime.tar.gz -C artifacts/boosting
fetch https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/ea30d66debe3740a08b573244286791d423d6b3e/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf \
  artifacts/boosting/model.gguf 3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1
clang++ -std=c++17 -O2 -Wall -Wextra -Werror experiments/voiceflow-v1/native-boost.cpp \
  -I artifacts/boosting/nemo-speech/include -L artifacts/boosting/nemo-speech/lib \
  -lnemo_speech_asr_c -Wl,-rpath,@executable_path/nemo-speech/lib \
  -o artifacts/boosting/native-boost
