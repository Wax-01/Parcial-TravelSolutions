"""Static catalogue of destinations and routes to keep synchronized."""

DESTINATIONS = {
    "BOG": "Bogotá", "MDE": "Medellín", "CTG": "Cartagena", "CLO": "Cali",
    "SMR": "Santa Marta", "PEI": "Pereira", "MIA": "Miami", "MAD": "Madrid",
}

ROUTES = [
    ("BOG", "MDE"), ("MDE", "BOG"), ("BOG", "CTG"), ("CTG", "BOG"), ("BOG", "CLO"), ("BOG", "SMR"),
    ("MDE", "CTG"), ("BOG", "PEI"), ("BOG", "MIA"), ("MDE", "MIA"), ("BOG", "MAD"), ("MIA", "BOG"),
]
