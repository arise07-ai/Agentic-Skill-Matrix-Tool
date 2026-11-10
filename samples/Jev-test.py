import os
from dotenv import load_dotenv

load_dotenv(".env")  # reads backend/.env
print("key found:", bool(os.environ.get("TYPESAFE_API_KEY")))

from typesafe_sdk import Choice, TypeSafeClient

with TypeSafeClient() as client:
    r = client.system_one(
        state={"skill": "ReactJS", "candidates": ["React", "React Native", "Redux"]},
        questions={"match": Choice(
            instructions="Which candidate skill does this spelling mean? Use 'none' if no candidate is the same skill.",
            criteria={"React": None, "React Native": None, "Redux": None, "none": None})},
    )
a = r.choices["match"]
print(a.choice)
print([x for x in dir(a) if not x.startswith("_")])
print(a)