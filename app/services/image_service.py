import pytesseract
from PIL import Image
from openai import OpenAI

client = OpenAI()

def extract_text_from_image(file_path: str) -> str:
    img = Image.open(file_path)
    return pytesseract.image_to_string(img)

def analyze_image_with_ai(text: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Você é uma secretária médica."},
            {"role": "user", "content": f"O paciente enviou esse exame:\n{text}\nExplique e diga o que fazer."}
        ]
    )
    return response.choices[0].message.content