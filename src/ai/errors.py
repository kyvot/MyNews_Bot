class SummaryError(Exception):
    """Base error for article extraction and summarization."""


class SummaryConfigurationError(SummaryError):
    pass


class ArticleFetchError(SummaryError):
    pass


class ArticleExtractionError(SummaryError):
    pass


class AIRequestError(SummaryError):
    pass


class AIInvalidResponseError(SummaryError):
    pass


class TranslationError(SummaryError):
    pass
