from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Listing:
    """One ad, normalised across sources."""

    source: str
    listing_id: str
    title: str
    url: str
    price: int | None  # ILS; None when the ad doesn't state a price
    description: str = ""
    city: str = ""
    condition: str = ""
    condition_id: str = ""
    product_type: str = ""
    product_type_id: str = ""
    image_url: str = ""
    previous_price: int | None = None
    is_business: bool = False

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"

    def text(self) -> str:
        """Everything keyword rules are matched against."""
        return "\n".join(part for part in (self.title, self.description, self.condition) if part)
