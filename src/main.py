from dotenv import load_dotenv
load_dotenv()

from anthropic import Anthropic

from os import getenv

client = Anthropic()
model = getenv("ANTHROPIC_MODEL", "claude-3.0")

message = client.messages.create(
    model=model,
    messages=[
        {
            "role": "user",
            "content": "Write a short poem about the beauty of nature."
        }
    ],
    max_tokens=100
)

print(message.content[0].text)