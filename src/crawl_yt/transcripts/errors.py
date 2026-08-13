"""Lightweight transcript failure taxonomy."""


class TranscriptProviderError(RuntimeError):
    pass


class NoSubtitleError(TranscriptProviderError):
    pass


class TransientSubtitleError(TranscriptProviderError):
    pass


class UnavailableVideoError(TranscriptProviderError):
    pass


class ProviderUnavailableError(TranscriptProviderError):
    pass


class TranscriptionError(TranscriptProviderError):
    pass
