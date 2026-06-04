"""Personal LoRA schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrainingConfig:
    """LoRA training config."""

    base_model: str = "meta-llama/Llama-3.1-8B"
    rank: int = 16  # 8-64 typical
    alpha: int = 32  # 通常 2 * rank
    target_modules: tuple[str, ...] = ("q_proj", "v_proj")  # attention
    learning_rate: float = 3e-4
    epochs: int = 3
    batch_size: int = 4
    gradient_accumulation_steps: int = 4

    # 数据来源
    conversation_log_path: str = "~/.sisoul/history/"
    min_conversations: int = 1000
    max_conversations: int = 10000


@dataclass
class LoRAAdapter:
    """Trained LoRA adapter."""

    name: str
    version: str
    base_model: str
    rank: int
    file_path: str  # ~/.sisoul/lora/<name>.safetensors
    size_bytes: int
    did_owner: str
    trained_at: str
    train_data_summary: dict = field(default_factory=dict)
    eval_metrics: dict = field(default_factory=dict)
