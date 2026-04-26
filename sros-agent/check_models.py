# import os
# from google import genai
# from dotenv import load_dotenv

# load_dotenv()

# client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# print(f"{'MODEL NAME':<40} | {'ACTIONS'}")
# print("-" * 70)

# # In the 2026 SDK, we iterate through the models directly
# for model in client.models.list():
#     # 'supported_actions' replaces 'supported_generation_methods'
#     actions = getattr(model, 'supported_actions', [])
    
#     # We want to see models that can either Chat or Embed
#     if "generate_content" in actions or "embed_content" in actions:
#         action_str = ", ".join(actions)
#         print(f"{model.name:<40} | {action_str}")


import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)        