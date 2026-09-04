"""Churn Radar — 個人運営SaaS向けの解約予兆検知。"""

from .detect import Signal, detect, load_events

__all__ = ["Signal", "detect", "load_events"]
