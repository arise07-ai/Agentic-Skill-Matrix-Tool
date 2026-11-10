"""FINE-TUNING  (Unsloth) - offline, needs a GPU. NOT part of the web app runtime.

Install in a SEPARATE environment:  pip install -r requirements-finetune.txt
Run:  python unsloth_train.py skill_norm.jsonl

This is a starting skeleton for a small open model (LoRA) on the skill-standardization task.
Choose base model / hyperparameters yourself, then serve the result behind an OpenAI-compatible
endpoint (vLLM, Ollama, ...) and point OPENAI_BASE_URL + LLM_MODEL at it.
Untested: written against Unsloth's documented FastLanguageModel + TRL SFTTrainer flow.
"""
import sys

BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct"  # TODO (you): pick your base model


def main(dataset_path: str) -> None:
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    model, tok = FastLanguageModel.from_pretrained(BASE_MODEL, max_seq_length=2048, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])
    ds = load_dataset("json", data_files=dataset_path, split="train")
    ds = ds.map(lambda ex: {"text": tok.apply_chat_template(ex["messages"], tokenize=False)})
    SFTTrainer(
        model=model, tokenizer=tok, train_dataset=ds,
        args=SFTConfig(output_dir="outputs", per_device_train_batch_size=2, num_train_epochs=2, learning_rate=2e-4, dataset_text_field="text"),
    ).train()
    model.save_pretrained("outputs/lora")


if __name__ == "__main__":
    main(sys.argv[1])
