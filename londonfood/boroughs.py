"""The 33 local authority areas that make up Greater London (OSM boundary names)."""

BOROUGHS = [
    "City of London",
    "City of Westminster",
    "Royal Borough of Kensington and Chelsea",
    "Royal Borough of Greenwich",
    "Royal Borough of Kingston upon Thames",
    "London Borough of Barking and Dagenham",
    "London Borough of Barnet",
    "London Borough of Bexley",
    "London Borough of Brent",
    "London Borough of Bromley",
    "London Borough of Camden",
    "London Borough of Croydon",
    "London Borough of Ealing",
    "London Borough of Enfield",
    "London Borough of Hackney",
    "London Borough of Hammersmith and Fulham",
    "London Borough of Haringey",
    "London Borough of Harrow",
    "London Borough of Havering",
    "London Borough of Hillingdon",
    "London Borough of Hounslow",
    "London Borough of Islington",
    "London Borough of Lambeth",
    "London Borough of Lewisham",
    "London Borough of Merton",
    "London Borough of Newham",
    "London Borough of Redbridge",
    "London Borough of Richmond upon Thames",
    "London Borough of Southwark",
    "London Borough of Sutton",
    "London Borough of Tower Hamlets",
    "London Borough of Waltham Forest",
    "London Borough of Wandsworth",
]


def short_name(borough):
    """'London Borough of Camden' -> 'Camden'."""
    for prefix in ("London Borough of ", "Royal Borough of ", "City of "):
        if borough.startswith(prefix) and borough != "City of London":
            return borough[len(prefix):]
    return borough
