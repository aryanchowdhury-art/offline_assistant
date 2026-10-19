from collections.abc import Iterator

import llama_cpp

from app.config import AppConfig


class LlamaEngine:
    """Thin wrapper around llama-cpp-python with lazy loading."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.llm: llama_cpp.Llama | None = None

    def load(self) -> llama_cpp.Llama:
        if self.llm is not None:
            return self.llm

        kwargs: dict = dict(
            model_path=str(self.config.model_path),
            n_ctx=self.config.n_ctx,
            n_threads=self.config.n_threads,
            n_batch=self.config.n_batch,
            use_mmap=True,
            use_mlock=False,
            verbose=False,
        )
        if self.config.force_chat_format:
            kwargs["chat_format"] = self.config.force_chat_format

        self.llm = llama_cpp.Llama(**kwargs)
        return self.llm

    def unload(self) -> None:
        self.llm = None

    def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> str:
        llm = self.load()
        out = llm(
            prompt,
            temperature=temperature,
            top_p=self.config.top_p,
            repeat_penalty=self.config.repeat_penalty,
            max_tokens=max_tokens or self.config.max_tokens,
            stop=stop or [],
            echo=False,
        )
        return out["choices"][0]["text"]

    def stream_chat_completion(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        llm = self.load()

        try:
            stream = llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                top_p=self.config.top_p,
                repeat_penalty=self.config.repeat_penalty,
                max_tokens=max_tokens or self.config.max_tokens,
                stream=True,
            )
        except ValueError as exc:
            if "chat template" in str(exc).lower():
                stream = llm.create_chat_completion(
                    messages=messages,
                    chat_format="chatml",
                    temperature=temperature,
                    top_p=self.config.top_p,
                    repeat_penalty=self.config.repeat_penalty,
                    max_tokens=max_tokens or self.config.max_tokens,
                    stream=True,
                )
            else:
                raise

        for chunk in stream:
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content