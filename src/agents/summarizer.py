from pydantic import BaseModel, Field
from pydantic_ai import Agent
from src.config.agent_config import model, shared_model_settings

class SummaryOutput(BaseModel):
    """Schema for AI generated summaries."""
    summary: str = Field(description="A professional, 1-sentence summary of the tech opportunity.")

summarizer_agent = Agent(
    model,
    output_type=SummaryOutput,
    model_settings=shared_model_settings,
    system_prompt=(
        "You are a technical content summarizer for Mu-Hive. "
        "Your goal is to write a crisp, professional, and engaging 1-sentence summary "
        "of a tech opportunity (hackathon or news) given its title and Interest Group context. "
        "Avoid filler words. Focus on the 'what' and 'why'."
    )
)
