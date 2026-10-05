"""
Setup:
    pip install -r requirements.txt
    echo "GEMINI_API_KEY=your_key_here" > .env
    python3 car_model_identifier.py car7.jpg   

Usage:
    python car_model_identifier.py path/to/photo.jpg
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai
from PIL import Image


def get_client() -> genai.Client:
    """Load the API key from .env / the environment and build a client."""
    load_dotenv()  # reads a .env file in the current folder, if present

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Create a .env file in this folder "
            "with a line: GEMINI_API_KEY=your_key_here"
        )

    return genai.Client(api_key=api_key)


MODEL = "gemini-3.6-flash"


def identify_car(image_path: str, client: genai.Client) -> str:
    """Upload a local image and ask Gemini to identify the car in it."""
    uploaded_file = client.files.upload(file=image_path)

    prompt = (
        "Look at this photo of a car and identify it. Reply in this exact format:\n"
        "Make: <manufacturer>\n"
        "Model: <model name>\n"
        "Approximate year / generation: <your best estimate, or a range>\n"
        "Confidence: <High / Medium / Low>\n"
        "Notes: <anything that helped you identify it, or reasons for uncertainty>\n\n"
        "If you cannot confidently identify the exact model, say so in the Notes and give your best guess anyway."
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=[uploaded_file, prompt],
    )

    return response.text


def show_image_info(image_path: str) -> None:
    """Console stand-in for the notebook's inline display(Image.open(...))."""
    with Image.open(image_path) as img:
        print(f"Loaded image: {image_path} ({img.width}x{img.height}, {img.mode})")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify the make/model/year of a car from a photo using Gemini."
    )
    parser.add_argument("image_path", help="Path to the car photo, e.g. car7.jpg")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not os.path.isfile(args.image_path):
        print(f"Error: file not found: {args.image_path}", file=sys.stderr)
        sys.exit(1)

    show_image_info(args.image_path)

    client = get_client()
    result = identify_car(args.image_path, client)
    print(result)


if __name__ == "__main__":
    main()