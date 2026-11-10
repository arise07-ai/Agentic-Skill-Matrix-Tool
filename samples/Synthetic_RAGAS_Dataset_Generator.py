import os
import json
from openai import OpenAI
from pydantic import BaseModel, Field

# 1. Initialize the official OpenAI Client
client = OpenAI(
    # By default, it will look for an environment variable named OPENAI_API_KEY.
    # Alternatively, you can paste it directly here like: api_key="sk-proj-..."
    api_key=os.environ.get("OPENAI_API_KEY", "REMOVED_KEY")
)

# 2. Define the schema structure using Pydantic
class SyntheticEvalCase(BaseModel):
    question: str = Field(description="A realistic, slightly messy enterprise user query.")
    context: str = Field(description="The underlying database or text snippet retrieved from the RAG tool.")
    ground_truth: str = Field(description="The ideal, 100% accurate response derived strictly from the context.")

# 3. Define the 4 core pillars
pillars = [
    "Skill Standardization",
    "Skill Gap Prediction",
    "Project Team Assembly",
    "Learning Path Suggestion"
]

# TARGET: 50 records per pillar
RECORDS_PER_PILLAR = 50
synthetic_dataset = []

# 4. Run the scaled generation loop
for pillar in pillars:
    print(f"\n--- Starting generation for Pillar: [{pillar}] ---")

    for i in range(1, RECORDS_PER_PILLAR + 1):
        print(f"Generating record {i}/{RECORDS_PER_PILLAR}...")

        # We tell the model what number we are on to help force diversity across calls
        prompt = f"""
        You are an expert synthetic data generator for enterprise Skill Matrix RAG applications.
        Generate a highly realistic, completely unique evaluation data point for the module: '{pillar}'.

        This is test case number {i} out of {RECORDS_PER_PILLAR}. 
        To ensure a robust evaluation dataset, dynamically pick a unique business or technology domain 
        (e.g., Finance, HealthTech, AI, Logistics, GameDev, Cybersecurity, retail, etc.) and a specific role/scenario 
        that hasn't been overused.

        Guidelines:
        1. 'question': Sound like a real manager or HR specialist. Use realistic phrasing, professional shorthand, or real business intent. Do not make it too perfect.
        2. 'context': This must look like a raw markdown or text snippet from an enterprise vector database (e.g., CV summaries, skill standards, course catalogs).
        3. 'ground_truth': The definitive 'perfect' answer derived logically from the context.
        """

        try:
            response = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a strict data generation tool. You only output structured JSON matching the requested schema."},
                    {"role": "user", "content": prompt}
                ],
                response_format=SyntheticEvalCase,
            )

            raw_json = response.choices[0].message.parsed

            synthetic_dataset.append({
                "question": raw_json.question,
                "contexts": [raw_json.context],
                "answer": "PLACEHOLDER",  # Overwritten during your RAG pipeline tests
                "ground_truth": raw_json.ground_truth,
                "metadata": {
                    "pillar": pillar,
                    "record_index": i
                }
            })
        except Exception as e:
            print(f"Error generating record {i} for {pillar}: {e}")
            continue

# 5. Export results to your local project directory
output_filename = "ragas_synthetic_dataset_200.json"
with open(output_filename, "w", encoding="utf-8") as f:
    json.dump(synthetic_dataset, f, indent=4, ensure_ascii=False)

print(f"\n🚀 Success! Complete dataset of {len(synthetic_dataset)} records generated and saved to: {output_filename}")
