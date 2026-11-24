from typing import Union, Optional
from pydantic import BaseModel, Field


class PricePredictionRequest(BaseModel):
    property_type: str = Field(..., description="Property type: 'Căn hộ' or 'Nhà đất'")
    area: float = Field(..., gt=0, description="Area in square meters")
    bedrooms: int = Field(..., ge=1, description="Number of bedrooms")
    latitude: float = Field(..., description="Latitude coordinate in HCMC")
    longitude: float = Field(..., description="Longitude coordinate in HCMC")
    district: str = Field(..., description="District or ward name")
    legal_status: Optional[str] = Field("Sổ hồng riêng", description="Legal status")