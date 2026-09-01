"""
Team-name aliases for the season import.

Any spelling a manager might type in the Excel workbook (club column, or the GK
team cell) mapped to the canonical FPL full_name — i.e. what appears in
players.csv full_name for type=team.

Keys must be lower-case; import_season.py lower-cases and strips the workbook
value before looking it up here.

Add new entries whenever a club is promoted into the Premier League, or when a
workbook turns up a spelling that isn't covered yet.
"""

TEAM_ALIASES: dict[str, str] = {
    # Arsenal
    "arsenal": "Arsenal",
    # Aston Villa
    "aston villa": "Aston Villa",
    "villa": "Aston Villa",
    "a.villa": "Aston Villa",
    "avfc": "Aston Villa",
    # Bournemouth
    "bournemouth": "Bournemouth",
    "afc bournemouth": "Bournemouth",
    "afcb": "Bournemouth",
    # Brentford
    "brentford": "Brentford",
    "brentord": "Brentford",  # common typo
    # Brighton
    "brighton": "Brighton",
    "brighton & hove albion": "Brighton",
    "brighton and hove albion": "Brighton",
    "brighton ha": "Brighton",
    "brighton hove albion": "Brighton",
    "bha": "Brighton",
    # Burnley
    "burnley": "Burnley",
    # Chelsea
    "chelsea": "Chelsea",
    # Crystal Palace
    "crystal palace": "Crystal Palace",
    "c.palace": "Crystal Palace",
    "cpfc": "Crystal Palace",
    "palace": "Crystal Palace",
    # Everton
    "everton": "Everton",
    # Fulham
    "fulham": "Fulham",
    # Ipswich
    "ipswich": "Ipswich",
    "ipswich town": "Ipswich",
    # Leeds
    "leeds": "Leeds",
    "leeds united": "Leeds",
    "lufc": "Leeds",
    # Leicester
    "leicester": "Leicester",
    "leicester city": "Leicester",
    "lcfc": "Leicester",
    # Liverpool
    "liverpool": "Liverpool",
    "lfc": "Liverpool",
    # Luton
    "luton": "Luton",
    "luton town": "Luton",
    # Man City
    "man city": "Man City",
    "man. city": "Man City",
    "manchester city": "Man City",
    "mancity": "Man City",
    "mcfc": "Man City",
    # Man Utd
    "man utd": "Man Utd",
    "man united": "Man Utd",
    "man. utd": "Man Utd",
    "manchester united": "Man Utd",
    "manchester utd": "Man Utd",
    "manutd": "Man Utd",
    "mufc": "Man Utd",
    # Middlesbrough
    "middlesbrough": "Middlesbrough",
    "boro": "Middlesbrough",
    # Newcastle
    "newcastle": "Newcastle",
    "newcastle united": "Newcastle",
    "newcastle utd": "Newcastle",
    "nufc": "Newcastle",
    # Norwich
    "norwich": "Norwich",
    "norwich city": "Norwich",
    "ncfc": "Norwich",
    # Nott'm Forest
    "nottingham forest": "Nott'm Forest",
    "notts forest": "Nott'm Forest",
    "nott'm forest": "Nott'm Forest",
    "nottm forest": "Nott'm Forest",
    "nffc": "Nott'm Forest",
    "forest": "Nott'm Forest",
    # Sheffield Utd
    "sheffield utd": "Sheffield Utd",
    "sheffield united": "Sheffield Utd",
    "sheffield": "Sheffield Utd",
    "sufc": "Sheffield Utd",
    # Sunderland
    "sunderland": "Sunderland",
    "safc": "Sunderland",
    # Southampton
    "southampton": "Southampton",
    "saints": "Southampton",
    "sfc": "Southampton",
    # Spurs / Tottenham
    "spurs": "Spurs",
    "tottenham": "Spurs",
    "tottenham hotspur": "Spurs",
    "thfc": "Spurs",
    # Swansea
    "swansea": "Swansea",
    "swansea city": "Swansea",
    "scfc": "Swansea",
    # Watford
    "watford": "Watford",
    "wfc": "Watford",
    # West Brom
    "west brom": "West Brom",
    "west bromwich albion": "West Brom",
    "wba": "West Brom",
    # West Ham
    "west ham": "West Ham",
    "west ham united": "West Ham",
    "west ham utd": "West Ham",
    "hammers": "West Ham",
    "whufc": "West Ham",
    # Wolves
    "wolves": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "wolverhampton": "Wolves",
    "wwfc": "Wolves",
}
