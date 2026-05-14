from .models import EvalMetrics, EvalFailedCase, EvalReport

__all__ = ["EvalMetrics", "EvalFailedCase", "EvalReport", "EvalRunner"]


def __getattr__(name):
    if name == "EvalRunner":
        from .runner import EvalRunner

        return EvalRunner
    raise AttributeError(name)
