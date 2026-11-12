"""Proveedores FAKE — EXCLUSIVAMENTE PARA TESTS. NO son funcionalidad real.

Estos stubs implementan los ``Protocol`` de ``providers.base`` sin hacer ninguna
llamada externa (ni Ollama, ni red, ni modelos). Existen para poder probar el
registry, la cola de jobs y el cableado de la API sin depender de servicios que no
están disponibles en el sandbox. Los proveedores reales llegan en FEAT-003/004.

No registrar estos fakes como default de ninguna capacidad: se exponen bajo el
nombre de backend ``"fake"`` y deben usarse solo desde tests.
"""

from __future__ import annotations

from pathlib import Path

from app.providers.base import AudioAsset, ImageAsset, VoiceLine, WordTiming


class FakeLLM:
    """LLM de test: devuelve un eco determinista del prompt, sin red."""

    name = "fake"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
        **opts: object,
    ) -> str:
        return f"[fake-llm] {prompt}"


class FakeImage:
    """ImageProvider de test: no descarga ni genera; devuelve una ruta ficticia."""

    name = "fake"

    def image_for(
        self,
        query: str,
        *,
        aspect: str,
        out_dir: str,
        seed: int | None = None,
    ) -> ImageAsset:
        path = str(Path(out_dir) / f"fake_{abs(hash(query)) % 100000}.png")
        return ImageAsset(
            path=path, width=1920, height=1080, provider=self.name, query=query, seed=seed
        )


class FakeTTS:
    """TTSProvider de test: devuelve un AudioAsset por línea, sin sintetizar audio."""

    name = "fake"

    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]:
        out: list[AudioAsset] = []
        for i, line in enumerate(lines):
            path = str(Path(out_dir) / f"fake_voice_{i}.wav")
            out.append(
                AudioAsset(path=path, duration_s=float(len(line.text)) / 15.0, provider=self.name)
            )
        return out


class FakeASR:
    """ASRProvider de test: alinea palabras con tiempos sintéticos uniformes."""

    name = "fake"

    def transcribe(self, audio_path: str) -> list[WordTiming]:
        return [WordTiming(word="palabra", start_s=0.0, end_s=0.5)]
