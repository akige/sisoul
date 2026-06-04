"""Personal LoRA trainer stub — full impl in v2.0 ship (T+12m)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import LoRAAdapter, TrainingConfig


@dataclass
class TrainingResult:
    adapter: LoRAAdapter
    train_loss_final: float
    eval_metrics: dict
    epoch_count: int


class PersonalLoRATrainer:
    """Skeleton for personal LoRA training pipeline.

    Foundation impl: validates config + returns mock adapter.
    Full impl (v2.0 ship): PEFT + HuggingFace + actual training loop.
    """

    def __init__(self, config: TrainingConfig, did_owner: str):
        self.config = config
        self.did_owner = did_owner
        if not did_owner.startswith("did:key:"):
            raise ValueError(f"invalid did_owner: {did_owner}")

    def collect_dataset(self, conversation_log_path: str | Path) -> int:
        """Walk ~/.sisoul/history/ to count conversations.

        Foundation impl: counts JSON files. Full impl: parses + preprocesses to SFT format.
        """
        path = Path(conversation_log_path).expanduser()
        if not path.exists():
            return 0
        return sum(1 for _ in path.glob("**/*.json"))

    def train(self, output_path: str | Path) -> TrainingResult:
        """Skeleton: returns mock adapter without actual training.

        Full impl (v2.0): peft.LoraConfig + transformers.Trainer.
        """
        outdir = Path(output_path).expanduser()
        outdir.parent.mkdir(parents=True, exist_ok=True)
        # mock safetensors file (full impl writes real)
        outdir.write_bytes(b"FOUNDATION_STUB_LORA_PLACEHOLDER")
        adapter = LoRAAdapter(
            name=f"personal-{self.did_owner[-8:]}",
            version=self.config.base_model.split("/")[-1],
            base_model=self.config.base_model,
            rank=self.config.rank,
            file_path=str(outdir),
            size_bytes=outdir.stat().st_size,
            did_owner=self.did_owner,
            trained_at="2026-06-04T00:00:00Z",
            train_data_summary={"stub": True, "min_conversations": self.config.min_conversations},
            eval_metrics={"stub": True},
        )
        return TrainingResult(
            adapter=adapter,
            train_loss_final=0.0,
            eval_metrics={"stub": True},
            epoch_count=self.config.epochs,
        )


__all__ = ["PersonalLoRATrainer", "TrainingResult"]
