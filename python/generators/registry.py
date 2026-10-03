"""
generators/registry.py — name -> Generator lookup. This is the actual
"plugin" mechanic: a new backend registers itself under a string name;
nothing that calls get_generator() needs to know the class exists.
"""

from typing import Dict, Type
from .base import Generator
from .rule_based import RuleBasedGenerator
from .cloud import CloudGenerator
from .local_model import LocalModelGenerator

_REGISTRY: Dict[str, Type[Generator]] = {
    "local:rule_based": RuleBasedGenerator,
    "local:model": LocalModelGenerator,
    "cloud:anthropic": CloudGenerator,
}


def register(name: str, cls: Type[Generator]) -> None:
    """Add a new backend, e.g. register('cloud:openai', OpenAIGenerator)."""
    _REGISTRY[name] = cls


def available() -> list:
    return sorted(_REGISTRY.keys())


def get_generator(name: str, **init_kwargs) -> Generator:
    if name not in _REGISTRY:
        raise KeyError(f"unknown generator '{name}', available: {available()}")
    return _REGISTRY[name](**init_kwargs)
