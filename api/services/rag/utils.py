"""Utility functions for RAG ingestion and processing."""

import re
from typing import Dict, Any

def calculate_image_to_text_ratio(markdown_content: str) -> float:
    """Calculate the ratio of image placeholders to total text length."""
    images = re.findall(r"!\[.*?\]\(.*?\)", markdown_content)
    text_length = len(markdown_content)
    if text_length == 0:
        return 0.0
    return len(images) / (text_length / 1000.0)  # Images per 1000 characters

def get_conversion_quality_report(markdown_content: str) -> Dict[str, Any]:
    """Generate a quality report for a converted document chunk."""
    return {
        "length": len(markdown_content),
        "has_tables": "|" in markdown_content and "-|-" in markdown_content,
        "has_code": "```" in markdown_content,
        "image_density": calculate_image_to_text_ratio(markdown_content),
        "is_stub": len(markdown_content) < 100,
    }
