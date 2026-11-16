
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

response = client.chat.completions.create(
    model=os.getenv("GROQ_MODEL"),
    messages=[
        {
            "role": "user",
            "content": "Explain what a logistics data pipeline is in two sentences."
        }
    ],
    temperature=0
)

print(response.choices[0].message.content)
