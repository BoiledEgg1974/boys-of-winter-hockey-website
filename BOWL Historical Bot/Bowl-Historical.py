import asyncio
import json
import os
import sys
from pathlib import Path
from time import monotonic

import pandas as pd
import discord
import urllib.error
import urllib.request


def _load_env_from_bot_dir() -> None:
    """Load KEY=value from .env next to this script so double-click / Explorer runs see DISCORD_BOT_TOKEN."""
    try:
        env_path = Path(__file__).resolve().parent / ".env"
        if not env_path.is_file():
            return
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key:
                continue
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_env_from_bot_dir()

# Define the intents
intents = discord.Intents.default()  # This enables the default intents
intents.messages = True  # Ensure the bot can receive messages
intents.guilds = True  # Ensure the bot can interact with guild information
intents.message_content = True  # Enable message content intent

# Initialize the client with the specified intents
client = discord.Client(intents=intents)

# Edit these to your own csv paths.
player_master_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_master.csv'
player_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_skater_stats_rs.csv'
player_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_skater_stats_po.csv'
player_ratings_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_ratings.csv'
team_data_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/team_data.csv'
team_records_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/team_records.csv'
goalie_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_goalie_stats_rs.csv'
goalie_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Historical.lg/import_export/csv/player_goalie_stats_po.csv'

team_emote_dict = {'MTL': '<:MTL:1358674505853046814>',
                   'TOR': '<:TOR:1479530374395723895>',
                   'BOS': '<:BOS:1296221296371306536>',
                   'CHI': '<:CHI:1391961982235705436>',
                   'DET': '<:DET:1290119897803915296>',
                   'NYR': '<:NYR:1479530385737257124>',
                   'OAK': '<:OAK:1469123074694840320>',
                   'PIT': '<:PIT:1469123007896223849>',
                   'PHI': '<:PHI:1469123032009146589>',
                   'STL': '<:STL:1469123043769974915>',
                   'LAK': '<:LAK:1469123020680597668>',
                   'MIN': '<:MIN:1469123055761489941>',}

# Option B: each command only works in a channel whose name matches (case-insensitive).
COMMAND_TO_CHANNEL_NAME = {
    "!help": "test",
    "!stats": "test",
    "!standings": "standings",
    "!gap": "goals-assists-points",
    "!artross": "art-ross",
    "!richard": "richard",
    "!norris": "norris",
    "!bourque": "bourque",
    "!langway": "langway",
    "!selke": "selke",
    "!ladybyng": "lady-byng",
    "!shots": "shots",
    "!pminus": "plus-minus",
    "!green": "green-jacket",
    "!bs": "blocked-shots",
    "!hits": "hits",
    "!fights": "fights",
    "!pim": "penalty-minutes",
    "!ppg": "power-play-goals",
    "!shg": "shorthanded-goals",
    "!gwg": "game-winning-goals",
    "!gva": "giveaways",
    "!tka": "takeaways",
    "!ovr": "overall-game-rating",
    "!grd": "defensive-game-rating",
    "!gro": "offensive-game-rating",
    "!vezina": "vezina",
    "!goaliew": "goalies-wins",
    "!gl": "goalies-losses",
    "!gaa": "goalies-gaa",
    "!saves": "goalies-saves",
    "!svp": "goalies-save-percentage",
    "!so": "goalies-shutouts",
    "!powerrank": "power-rankings",
    "!prospectrank": "prospect-rankings",
    "!positionalrank": "positional-rankings",
    "!calder": "calder-trophy",
}


def matches_command(content: str, command: str) -> bool:
    'Match exact !command; reject longer tokens like !something for !so.'
    s = content.strip()
    if not s.startswith(command):
        return False
    if len(s) == len(command):
        return True
    next_ch = s[len(command) : len(command) + 1]
    return not next_ch.isalnum()


def _channel_name_for_message(message: discord.Message) -> str:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        if isinstance(parent, discord.TextChannel):
            return (parent.name or "").casefold()
    if isinstance(ch, discord.TextChannel):
        return (ch.name or "").casefold()
    return ""


async def message_in_allowed_channel(message: discord.Message, command_key: str) -> bool:
    required = COMMAND_TO_CHANNEL_NAME.get(command_key)
    if not required:
        return True
    if _channel_name_for_message(message) == required.casefold():
        return True
    await message.channel.send(
        f"This command can only be used in **#{required}**."
    )
    return False


# Function to initialize the dataframes
def initialize_dataframes(playoffs = False):
    player_master_df = pd.read_csv(
        player_master_path,
        sep=';',
        usecols=['PlayerId', 'First Name', 'Last Name'],
        encoding='ISO-8859-1',
        low_memory=False  # Alternatively, specify dtype for each column
    )
    player_master_df['PlayerId'] = pd.to_numeric(player_master_df['PlayerId'], errors='coerce').fillna(-1).astype(int)

    if playoffs:
        player_stats_df = pd.read_csv(
            player_stats_path_po,
            sep=';',
            usecols=['PlayerId', 'TeamId', 'GP', 'G', 'A', 'PIM', 'GR', 'SH G', 'GWG', 'GvA', 'TkA', 'PP G', '+/-', 'Game Rating Off', 'Game Rating Def', 'SOG', 'HIT', 'SB', 'GA/60', 'FO', 'FOW', 'Fights'],
            encoding='ISO-8859-1',
            low_memory=False  # Alternatively, specify dtype for each column
        )
        player_stats_df['PlayerId'] = pd.to_numeric(player_stats_df['PlayerId'], errors='coerce').fillna(-1).astype(int)
    else:
        player_stats_df = pd.read_csv(
            player_stats_path_rs,
            sep=';',
            usecols=['PlayerId', 'TeamId', 'GP', 'G', 'A', 'PIM', 'GR', 'SH G', 'GWG', 'GvA', 'TkA', 'PP G', '+/-', 'Game Rating Off', 'Game Rating Def', 'SOG', 'HIT', 'SB', 'GA/60', 'FO', 'FOW', 'Fights'],
            encoding='ISO-8859-1',
            low_memory=False  # Alternatively, specify dtype for each column
        )
        player_stats_df['PlayerId'] = pd.to_numeric(player_stats_df['PlayerId'], errors='coerce').fillna(-1).astype(int)

    player_ratings_df = pd.read_csv(
        player_ratings_path,
        sep=';',
        usecols=['PlayerId', 'G', 'LD', 'RD', 'LW', 'RW', 'C'],
        encoding='ISO-8859-1',
        low_memory=False  # Alternatively, specify dtype for each column
    )
    player_ratings_df['PlayerId'] = pd.to_numeric(player_ratings_df['PlayerId'], errors='coerce').fillna(-1).astype(int)

    if playoffs:
        goalie_stats_df = pd.read_csv(
            goalie_stats_path_po,
            sep=';',
            usecols=['PlayerId', 'TeamId', 'Games Played', 'Wins', 'Losses', 'Goals Against Average', 'Saves', 'Shutouts', 'Save Percentage', 'Game Rating'],
            encoding='ISO-8859-1',
            low_memory=False  # Alternatively, specify dtype for each column
        )
        goalie_stats_df['PlayerId'] = pd.to_numeric(goalie_stats_df['PlayerId'], errors='coerce').fillna(-1).astype(int)
    else:
        goalie_stats_df = pd.read_csv(
            goalie_stats_path_rs,
            sep=';',
            usecols=['PlayerId', 'TeamId', 'Games Played', 'Wins', 'Losses', 'Goals Against Average', 'Saves', 'Shutouts', 'Save Percentage', 'Game Rating'],
            encoding='ISO-8859-1',
            low_memory=False  # Alternatively, specify dtype for each column
        )
        goalie_stats_df['PlayerId'] = pd.to_numeric(goalie_stats_df['PlayerId'], errors='coerce').fillna(-1).astype(int)

    team_data_df = pd.read_csv(
        team_data_path,
        sep=';',
        usecols=['TeamId', 'LeagueId', 'Name', 'Nickname', 'Abbr', 'Conference Id', 'Division Id'],
        encoding='ISO-8859-1',
        low_memory=False
        )
    team_data_df['TeamId'] = pd.to_numeric(team_data_df['TeamId'], errors='coerce').fillna(-1).astype(int)

    team_records_df = pd.read_csv(
        team_records_path,
        sep=';',
        usecols=['League Id', 'Team Id', 'Wins', 'Losses', 'Ties', 'Points', 'Goals For', 'Goals Against'],
        encoding='ISO-8859-1',
        low_memory=False
        )
    team_records_df['Team Id'] = pd.to_numeric(team_records_df['Team Id'], errors='coerce').fillna(-1).astype(int)

    
    return player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df

# Function to calculate statistics
def calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df):

    k = 1

    edited_ratings_df = player_ratings_df.rename(columns={'G': 'Goalie'})
    goalie_merged_df = pd.merge(player_master_df, goalie_stats_df, on='PlayerId')
    merged_df = pd.merge(player_master_df, player_stats_df, on='PlayerId')
    merged_df = pd.merge(merged_df, edited_ratings_df, on='PlayerId')
    merged_df['Pts'] = merged_df['G'] + merged_df['A']
    merged_df['Pts Adjusted'] = merged_df['Pts'] - (merged_df['PIM'] * k)
    min_gp = merged_df['GP'].max() * 0.6
    merged_df = merged_df[merged_df['GP'] >= min_gp]
    merged_df['FO%'] = (merged_df['FOW'] / merged_df['FO'] * 100).round(1).astype(str) + '%'
    return merged_df, goalie_merged_df

def calculate_team_stats(team_data_df, team_records_df):
    team_records_df_column_change = team_records_df.rename(columns={'Team Id': 'TeamId'})
    merged_df = pd.merge(team_data_df, team_records_df_column_change, on='TeamId')
    merged_df['GP'] = merged_df['Wins'] + merged_df['Losses'] + merged_df['Ties']
    sorted_df = sort_players_by_column(merged_df, 'Points')
    return sorted_df
    # WRITE THE STANDINGS FUNCTION WITH THE MERGED DATAFRAMES

def standings(sorted_df):
    east_msg = "## East Division\n"
    east_num = 0
    west_msg = "## West Division\n"
    west_num = 0
    for i, row in sorted_df.iterrows():
        conferenceid = row['Conference Id']
        divisionid = row['Division Id']
        leagueid = row['LeagueId']
        teamid = row['TeamId']
        teamabbr = row['Abbr']
        team = row['Name'] + ' ' + row['Nickname']
        gp = row['GP']
        wins = row['Wins']
        losses = row['Losses']
        ties = row['Ties']
        gf = row['Goals For']
        ga = row['Goals Against']
        points = row['Points']  # Make sure this is calculated or present in the DataFrame

        if leagueid == 0:
            if conferenceid == -1 and divisionid == 0: # east
                east_msg += (f"**{east_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                east_num += 1
            elif conferenceid == -1 and divisionid == 1: # west
                west_msg += (f"**{west_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                west_num += 1
            
    final_message = (f">>> {east_msg}————————————————", f">>> {west_msg}————————————————")
    return final_message

def goalsAssistsPoints(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        assists = row['A']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {points} PTS**\n"
                            f"{games_played} GP • {goals} G • {assists} A\n\n")
            num_of_players += 1
        message += player_line
    return message

def norris(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        assists = row['A']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame
        grd = row['Game Rating Def']
        pm = row['+/-']
        if pm > 0:
            pm = f'+{pm}'
        else:
            pm = f'{pm}'

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and (row['LD'] == 20 or row['RD'] == 20):
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {points} PTS**\n"
                            f"{games_played} GP • {goals} G • {assists} A • {grd} DEFGR • {pm} \n\n")
            num_of_players += 1
        message += player_line
    return message

def bourque(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        grd = row['Game Rating Def']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and (row['LD'] == 20 or row['RD'] == 20):
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {goals} G**\n"
                            f"{games_played} GP • {grd} DEFGR\n\n")
            num_of_players += 1
        message += player_line
    return message

def langway(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        assists = row['A']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame
        grd = row['Game Rating Def']
        pm = row['+/-']
        if pm > 0:
            pm = f'+{pm}'
        else:
            pm = f'{pm}'

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and (row['LD'] == 20 or row['RD'] == 20):
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {grd} DEFGR**\n"
                            f"{games_played} GP • {goals} G • {assists} A • {points} PTS • {pm}\n\n")
            num_of_players += 1
        message += player_line
    return message

def selke(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        assists = row['A']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame
        grd = row['Game Rating Def']
        pm = row['+/-']
        if pm > 0:
            pm = f'+{pm}'
        else:
            pm = f'{pm}'

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and (row['LW'] == 20 or row['C'] == 20 or row['RW'] == 20):
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {grd} DEFGR**\n"
                            f"{games_played} GP • {goals} G • {assists} A • {points} PTS • {pm}\n\n")
            num_of_players += 1
        message += player_line
    return message

def ladyByng(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        goals = row['G']
        games_played = row['GP']
        assists = row['A']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame
        pim = row['PIM']
        pts_adjusted = row['Pts Adjusted']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and (row['Goalie'] != 20):
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {pts_adjusted} PTS Adjusted: ({pim} PIM, {points} PTS)**\n"
                            f"{games_played} GP • {goals} G • {assists} A\n\n")
            num_of_players += 1
        message += player_line
    return message

def shots(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        shots = row['SOG']  # Make sure this is calculated or present in the DataFrame

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {shots} SOG**\n"
                           f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def plusMinus(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        plus_minus = row['+/-']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, " + "{0:+}".format(plus_minus) + "**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def blockedShots(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        blocked_shots = row['SB']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {blocked_shots} BS**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def penaltyMinutes(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        penalty_minutes = row['PIM']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {penalty_minutes} PIM**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def powerPlayGoals(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        power_play_goals = row['PP G']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {power_play_goals} PPG**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def shortHandedGoals(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        shorthanded_goals = row['SH G']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {shorthanded_goals} SHG**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def gameWinningGoals(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        game_winning_goals = row['GWG']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {game_winning_goals} GWG**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def giveaways(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        giveaways = row['GvA']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {giveaways} GvA**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def takeaways(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        takeaways = row['TkA']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {takeaways} TkA**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def hits(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        hits = row['HIT']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {hits} Hits**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def richard(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        g = row['G']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {g} G**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def overallGameRating(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        ovr = row['GR']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {ovr} OVR**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def defensiveGameRating(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        grd = row['Game Rating Def']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {grd} DEFGR**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def offensiveGameRating(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        gro = row['Game Rating Off']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {gro} OFFGR**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def goalieWins(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        wins = row['Wins']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {wins} Wins**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def goalieLosses(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        losses = row['Losses']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {losses} Losses**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1

        message += player_line
    return message

def goalieSaves(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        saves = row['Saves']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {saves} Saves**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def goalieGAA(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        gaa = row['Goals Against Average']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {gaa} GAA**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def goalieSavePercentage(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        save_percentage = row['Save Percentage']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, S% (dec.): {save_percentage} **\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def goalieShutouts(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        shutouts = row['Shutouts']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {shutouts} Shutouts**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def vezina(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['Games Played']
        shutouts = row['Shutouts']
        game_rating = row['Game Rating']
        save_percentage = row['Save Percentage']
        gaa = row['Goals Against Average']
        wins = row['Wins']
        losses = row['Losses']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {wins} W**\n"
                            f"{games_played} GP • {losses} L • {game_rating} GR • {shutouts} Shutouts • {gaa} GAA • {save_percentage} SVP\n\n")
            num_of_players += 1
        message += player_line
    return message

def fights(sorted_df, team_data_df):
    message = ""
    num_of_players = 0
    for i, row in sorted_df.iterrows():
        if num_of_players > 9:
            return message
        player_name = f"{row['First Name']} {row['Last Name']}"
        for j, team_data_row in team_data_df.iterrows():
            if team_data_row['TeamId'] == row['TeamId']:
                leagueid = team_data_row['LeagueId']
                teamid = team_data_row['TeamId']
                teamabbr = team_data_row['Abbr']
                team = team_data_row['Name'] + ' ' + team_data_row['Nickname']
                break
        games_played = row['GP']
        fights = row['Fights']

        # Constructing the line for each player
        player_line = ''
        if leagueid == 0 and row['Goalie'] != 20:
            player_line = (f"**{num_of_players+1}.** {team_emote_dict[teamabbr]} **{player_name}, {team}, {fights} Fights**\n"
                            f"{games_played} GP \n\n")
            num_of_players += 1
        message += player_line
    return message

def sort_players_by_column(df, column_name, ascending=False):
    """
    Sorts a DataFrame based on the given column name.

    Parameters:
    - df: pandas.DataFrame - The DataFrame to sort.
    - column_name: str - The name of the column to sort the DataFrame by.
    - ascending: bool - Determines if the sorting should be in ascending order. Defaults to False.

    Returns:
    - pandas.DataFrame - The sorted DataFrame.
    """
    sorted_df = df.sort_values(by=column_name, ascending=ascending)
    return sorted_df

# Formatting data for Discord
def format_for_discord(top_players_df, team_data_df):
    message = ""
    for i, row in top_players_df.iterrows():
        player_name = f"{row['First Name']} {row['Last Name']}"
        league = team_data_df.at[row['LeagueId'], 'Name']
        team = team_data_df.at[row['TeamId'], 'Name']  # Assuming 'TeamId' column contains team names or identifiers
        goals = row['G']
        games_played = row['GP']
        points = row['Pts']  # Make sure this is calculated or present in the DataFrame
        shots_on_goal = row['SOG']
        goals_per_60 = row['GA/60']  # Ensure this calculation is done beforehand
        power_play_goals = row['PP G']  # Assuming there's a 'PPG' column
        offensive_game_rating = row['Game Rating Off']  # Assuming there's an 'OGR' column or similar

        # Constructing the line for each player
        player_line = (f"**{i+1}. {team}, {player_name}, {goals}G**"
                       f"{games_played} GP • {points} Points • {shots_on_goal} SOG • "
                       f"{goals_per_60} GA/60 • {power_play_goals} PPG • {offensive_game_rating} OGR\n\n")
        message += player_line
    return message

async def dispatch_discord_command(content: str, channel):
    # Respond to the !help command
    if matches_command(content, '!help'):
        valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
        help_message = ("Here are the valid columns you can sort by:\n" +
                        ", ".join(valid_columns) +
                        "\nUse them with the !stats command. Example: `!stats,Game Rating Off`")
        await channel.send(help_message)
        return
    
    if matches_command(content, '!shots'):
        try:
            column_name = 'SOG'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## SHOTS\n—————\n'
            formatted_message += shots(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")
    
    if matches_command(content, '!gap'):
        try:
            column_name = 'Pts'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GOALS • ASSISTS • POINTS\n———————————————————\n'
            formatted_message += goalsAssistsPoints(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!richard'):
        try:
            column_name = 'G'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:RICH:1231984904544321621> RICHARD <:RICH:1231984904544321621>\n———————————\n'
            formatted_message += richard(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")
    
    if matches_command(content, '!norris'):
        try:
            column_name = 'Pts'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:NORR:1231984831164973207> NORRIS <:NORR:1231984831164973207>\n——————————\n'
            formatted_message += norris(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!bourque'):
        try:
            column_name = 'G'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:BOUR:1231984430726516767> BOURQUE <:BOUR:1231984430726516767>\n————————————\n'
            formatted_message += bourque(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!langway'):
        try:
            column_name = 'Game Rating Def'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:LANG:1231984758355918958> LANGWAY <:LANG:1231984758355918958>\n————————————\n'
            formatted_message += langway(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!selke'):
        try:
            column_name = 'Game Rating Def'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:SELK:1231984965130915860> SELKE <:SELK:1231984965130915860>\n——————————\n'
            formatted_message += selke(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!ladybyng'):
        try:
            column_name = 'Pts Adjusted'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:BYNG:1231984473290178590> LADY BYNG <:BYNG:1231984473290178590>\n—————————————\n```PTS Adjusted = Points - PIM```\n'
            formatted_message += ladyByng(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!vezina'):
        try:
            column_name = 'Wins'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:VEZI:1231985026166689838> VEZINA <:VEZI:1231985026166689838>\n——————————\n'
            formatted_message += vezina(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!goaliew'):
        try:
            column_name = 'Wins'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GOALIE WINS\n——————————\n'
            formatted_message += goalieWins(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!gl'):
        try:
            column_name = 'Losses'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GOALIE LOSSES\n————————————\n'
            formatted_message += goalieLosses(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!gaa'):
        try:
            column_name = 'Goals Against Average'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name, True)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GOALS AGAINST AVERAGE\n———————————————————\n'
            formatted_message += goalieGAA(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!saves'):
        try:
            column_name = 'Saves'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## SAVES\n——————\n'
            formatted_message += goalieSaves(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!svp'):
        try:
            column_name = 'Save Percentage'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## SAVE PERCENTAGE\n——————————————\n'
            formatted_message += goalieSavePercentage(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!so'):
        try:
            column_name = 'Shutouts'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## SHUTOUTS\n—————————\n'
            formatted_message += goalieShutouts(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")
    
    if matches_command(content, '!artross'):
        try:
            column_name = 'Pts'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:ROSS:1231984944755114037> ART ROSS <:ROSS:1231984944755114037>\n————————————\n'
            formatted_message += goalsAssistsPoints(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

## THIS NEEDS TO HAVE PO STATS INSTEAD
    if matches_command(content, '!conn'):
        try:
            column_name = 'Pts'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes(True)
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:CONN:1231984552873037834> CONN SMYTHE <:CONN:1231984552873037834> \n————————————————\n'
            formatted_message += goalsAssistsPoints(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")
        try:
            column_name = 'Wins'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes(True)
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(goalie_merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GOALIES BY WINS\n—————————————\n'
            formatted_message += vezina(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!pminus'):
        try:
            column_name = '+/-'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## PLUS/MINUS\n——————————\n'
            formatted_message += plusMinus(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!green'):
        try:
            column_name = '+/-'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name, True)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## <:GREEN:1231984659324342412> THE GREEN JACKET! <:GREEN:1231984659324342412>\n————————————————\n'
            formatted_message += plusMinus(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!bs'):
        try:
            column_name = 'SB'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## BLOCKED SHOTS\n—————————————\n'
            formatted_message += blockedShots(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!hits'):
        try:
            column_name = 'HIT'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## HITS\n————\n'
            formatted_message += hits(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!fights'):
        try:
            column_name = 'Fights'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## FIGHTS\n——————\n'
            formatted_message += fights(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!pim'):
        try:
            column_name = 'PIM'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## PENALTY MINUTES\n——————————————\n'
            formatted_message += penaltyMinutes(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")
    
    if matches_command(content, '!ppg'):
        try:
            column_name = 'PP G'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## POWER PLAY GOALS\n————————————————\n'
            formatted_message += powerPlayGoals(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!shg'):
        try:
            column_name = 'SH G'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## SHORT HANDED GOALS\n——————————————————\n'
            formatted_message += shortHandedGoals(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!gwg'):
        try:
            column_name = 'GWG'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GAME WINNING GOALS\n—————————————————\n'
            formatted_message += gameWinningGoals(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!gva'):
        try:
            column_name = 'GvA'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## GIVEAWAYS\n—————————\n'
            formatted_message += giveaways(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!tka'):
        try:
            column_name = 'TkA'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## TAKEAWAYS\n—————————\n'
            formatted_message += takeaways(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!ovr'):
        try:
            column_name = 'GR'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## OVERALL GAME RATING\n——————————————————\n'
            formatted_message += overallGameRating(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!grd'):
        try:
            column_name = 'Game Rating Def'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## DEFENSIVE GAME RATING\n———————————————————\n'
            formatted_message += defensiveGameRating(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!gro'):
        try:
            column_name = 'Game Rating Off'
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            merged_df, goalie_merged_df = calculate_stats(player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df)
            sorted_df = sort_players_by_column(merged_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in sorted_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            sorted_df = sorted_df.reset_index(drop=True)
            formatted_message = '## OFFENSIVE GAME RATING\n———————————————————\n'
            formatted_message += offensiveGameRating(sorted_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!standings'):
        try:
            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            sorted_df = calculate_team_stats(team_data_df, team_records_df)
            formatted_message = standings(sorted_df)
            await channel.send('## STANDINGS\n—————————————————\n')
            for division in formatted_message:
                await channel.send(division)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    if matches_command(content, '!stats'):
        try:
            _, column_name = content.split(',', 1)
            column_name = column_name.strip()
            # Ensure column_name is in the list of valid columns to prevent misuse
            valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
            if column_name not in valid_columns:
                await channel.send("Invalid column name. Use `!help` to see valid columns.")
                return

            player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = initialize_dataframes()
            top_players_df = calculate_stats(player_master_df, player_stats_df)
            merged_df = sort_players_by_column(top_players_df, column_name)

            # Verify that the column exists in the DataFrame
            if column_name not in merged_df.columns:
                raise ValueError(f"Column `{column_name}` not found.")

            top_players_df = merged_df.head(10).reset_index(drop=True)
            formatted_message = format_for_discord(top_players_df, team_data_df)
            await channel.send(formatted_message)
        except Exception as e:
            await channel.send(f"An error occurred: {str(e)}")

    for stub_cmd, label in (
        ("!powerrank", "Power rankings"),
        ("!prospectrank", "Prospect rankings"),
        ("!positionalrank", "Positional rankings"),
        ("!calder", "Calder / rookie spotlight"),
    ):
        if matches_command(content, stub_cmd):
            await channel.send(
                f"**{label}** — website Discord notifications post here when queued from BOWL. "
                "FHM CSV `!` output for this board is not implemented yet."
            )
            return

COMMAND_KEYS_SORTED = sorted(COMMAND_TO_CHANNEL_NAME.keys(), key=len, reverse=True)

# Set BOWL_SKIP_STARTUP_POST=1 to only react to manual !commands (no auto-post on launch).
STARTUP_POST_COMMANDS = os.environ.get("BOWL_SKIP_STARTUP_POST", "").lower() not in ("1", "true", "yes")

_startup_posts_done = False

_discord_event_poller_started = False
SITE_API_BASE_URL = os.environ.get('SITE_API_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
DISCORD_EVENTS_SHARED_SECRET = os.environ.get('DISCORD_EVENTS_SHARED_SECRET', 'bowluniverse').strip()
LEAGUE_SLUG = os.environ.get('LEAGUE_SLUG', 'bowl-historical').strip()
DISCORD_GUILD_ID = os.environ.get('DISCORD_GUILD_ID', '').strip()
BOT_HEARTBEAT_NAME = os.environ.get('BOT_HEARTBEAT_NAME', 'bowl-historical-bot').strip()
BOT_HEARTBEAT_VERSION = os.environ.get('BOT_HEARTBEAT_VERSION', '1.0').strip()
EVENT_IDEMPOTENCY_TTL_SECONDS = 6 * 60 * 60
EVENT_CHANNEL_BY_KEY = {
    'league-news': 'league-news',
    'transactions': 'transactions',
    'league-announcements': 'league-announcements',
    'staff-ops-alerts': 'staff-ops-alerts',
    'standings': 'standings',
    'goals-assists-points': 'goals-assists-points',
    'power-rankings': 'power-rankings',
    'prospect-rankings': 'prospect-rankings',
    'positional-rankings': 'positional-rankings',
    'calder-trophy': 'calder-trophy',
}


def _guilds_for_startup():
    raw = os.environ.get("BOWL_GUILD_ID", "").strip()
    if raw:
        g = client.get_guild(int(raw))
        return [g] if g else []
    return list(client.guilds)


def _find_text_channel_by_name(guild, name: str):
    want = name.casefold()
    for ch in guild.text_channels:
        if ch.name.casefold() == want:
            return ch
    return None


def _startup_payloads_for_command(cmd_key: str):
    if cmd_key == "!stats":
        return ["!stats,Pts"]
    return [cmd_key]


async def post_all_commands_on_startup():
    guilds = _guilds_for_startup()
    if not guilds:
        print("Startup posts: no guilds loaded (set BOWL_GUILD_ID if the bot is only in one server).")
        return
    for guild in guilds:
        if guild is None:
            continue
        print(f"Pushing startup leaderboards to guild: {guild.name}")
        for cmd_key, slug in COMMAND_TO_CHANNEL_NAME.items():
            ch = _find_text_channel_by_name(guild, slug)
            if ch is None:
                print(f"  Missing channel #{slug} for {cmd_key}")
                continue
            for payload in _startup_payloads_for_command(cmd_key):
                try:
                    await dispatch_discord_command(payload, ch)
                except Exception as e:
                    print(f"  Error {cmd_key} -> #{slug}: {e}")
                await asyncio.sleep(0.65)


@client.event
async def on_ready():
    global _startup_posts_done, _discord_event_poller_started
    print(f"Logged in as {client.user}")
    if STARTUP_POST_COMMANDS and not _startup_posts_done:
        _startup_posts_done = True
        await asyncio.sleep(1.5)
        try:
            await post_all_commands_on_startup()
        except Exception as e:
            print(f"Startup post failed: {e}")
        print("Startup posts finished.")
    if DISCORD_EVENTS_SHARED_SECRET and not _discord_event_poller_started:
        _discord_event_poller_started = True
        client.loop.create_task(discord_event_poller())


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    matched = None
    for cmd in COMMAND_KEYS_SORTED:
        if matches_command(message.content, cmd):
            matched = cmd
            break
    if matched is None:
        return
    if not await message_in_allowed_channel(message, matched):
        return
    await dispatch_discord_command(message.content, message.channel)


_delivered_event_keys = {}


def _seen_idempotency_key_recently(key: str) -> bool:
    k = str(key or '').strip()
    if not k:
        return False
    now = monotonic()
    cutoff = now - EVENT_IDEMPOTENCY_TTL_SECONDS
    stale = [x for x, ts in _delivered_event_keys.items() if ts < cutoff]
    for x in stale:
        _delivered_event_keys.pop(x, None)
    ts = _delivered_event_keys.get(k)
    return ts is not None and ts >= cutoff


def _remember_idempotency_key(key: str) -> None:
    k = str(key or '').strip()
    if not k:
        return
    _delivered_event_keys[k] = monotonic()


def _discord_api_json(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{SITE_API_BASE_URL}{path}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        method=method.upper(),
        headers={
            'Content-Type': 'application/json',
            'X-Discord-Events-Secret': DISCORD_EVENTS_SHARED_SECRET,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode('utf-8', errors='replace')
    return json.loads(raw or '{}')


def _channel_for_event_key(guild: discord.Guild, channel_key: str):
    want = EVENT_CHANNEL_BY_KEY.get(str(channel_key or '').strip(), str(channel_key or '').strip())
    if not want:
        return None
    for ch in guild.text_channels:
        if (ch.name or '').casefold() == want.casefold():
            return ch
    return None


async def _deliver_event_to_channel(ev: dict) -> None:
    guilds = _guilds_for_startup()
    if not guilds:
        raise RuntimeError('No guilds available for event delivery')
    guild = guilds[0]
    ch = _channel_for_event_key(guild, str(ev.get('channel_key') or ''))
    if ch is None:
        raise RuntimeError(f"Missing channel for key '{ev.get('channel_key')}'")
    payload = ev.get('payload') or {}
    event_key = str(ev.get('event_key') or '')
    if event_key == 'story_published':
        await ch.send(
            f"📰 Story published (schedule #{payload.get('schedule_id')}, article #{payload.get('article_id')})."
        )
    elif event_key == 'trade_request':
        await ch.send(
            f"✅ Ops request #{payload.get('request_id')} · {payload.get('request_type')} -> {payload.get('status')}."
        )
    elif event_key == 'standings_posted':
        await ch.send('📊 **Standings** update from the BOWL website.')
    elif event_key == 'statistical_leaders_posted':
        keys = payload.get('leader_command_keys') or []
        await ch.send(
            f"📈 **Statistical leaders** refresh from the BOWL website ({len(keys)} categories in payload)."
        )
    elif event_key == 'power_rankings_posted':
        await ch.send('🏒 **Power rankings** update from the BOWL website.')
    elif event_key == 'prospect_rankings_posted':
        await ch.send('🧢 **Prospect rankings** update from the BOWL website.')
    elif event_key == 'positional_rankings_posted':
        await ch.send('📋 **Positional rankings** update from the BOWL website.')
    elif event_key == 'calder_trophy_posted':
        await ch.send('🎖️ **Calder / rookie spotlight** update from the BOWL website.')
    elif event_key == 'announcement_posted':
        await ch.send(
            f"📢 {payload.get('level', 'info').upper()} announcement: {payload.get('title', 'Announcement')}"
        )
    elif event_key == 'control_center_restore':
        await ch.send(
            f"⚠️ Control Center restore executed from backup `{payload.get('backup_name')}`."
        )
    else:
        await ch.send(f"Event `{event_key}` received: {json.dumps(payload)[:600]}")


async def discord_event_poller():
    print(f"Discord event poller started: {LEAGUE_SLUG} via {SITE_API_BASE_URL}")
    while True:
        sent_count = 0
        last_error = ''
        try:
            res = _discord_api_json(
                'GET',
                f"/api/discord/events/pending?league_slug={LEAGUE_SLUG}&limit=10",
            )
            events = res.get('events') or []
            for ev in events:
                eid = int(ev.get('id'))
                idem = str(ev.get('idempotency_key') or '').strip()
                try:
                    if _seen_idempotency_key_recently(idem):
                        _discord_api_json('POST', f"/api/discord/events/{eid}/ack", {})
                        continue
                    await _deliver_event_to_channel(ev)
                    _discord_api_json('POST', f"/api/discord/events/{eid}/ack", {})
                    _remember_idempotency_key(idem)
                    sent_count += 1
                except Exception as ex:
                    last_error = str(ex)[:400]
                    _discord_api_json(
                        'POST',
                        f"/api/discord/events/{eid}/fail",
                        {'error': last_error},
                    )
        except Exception as e:
            print(f"Discord event poller error: {e}")
            last_error = str(e)[:400]
        try:
            guild_id = DISCORD_GUILD_ID or (str(client.guilds[0].id) if client.guilds else '')
            _discord_api_json(
                'POST',
                '/api/discord/events/heartbeat',
                {
                    'league_slug': LEAGUE_SLUG,
                    'bot_name': BOT_HEARTBEAT_NAME,
                    'bot_version': BOT_HEARTBEAT_VERSION,
                    'guild_id': guild_id,
                    'pending_count': len(events) if 'events' in locals() else 0,
                    'sent_count': sent_count,
                    'last_error': last_error,
                },
            )
        except Exception as hb_ex:
            print(f"Discord heartbeat error: {hb_ex}")
        await asyncio.sleep(15)


def _pause_before_close() -> None:
    if sys.platform == "win32":
        try:
            input("Press Enter to close...")
        except EOFError:
            pass


def main() -> None:
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        print(
            "ERROR: DISCORD_BOT_TOKEN is not set.\n"
            "Create a file named .env in this folder (see .env.example) containing:\n"
            "  DISCORD_BOT_TOKEN=your_token_from_discord_developer_portal\n",
            file=sys.stderr,
        )
        _pause_before_close()
        raise SystemExit(1)
    client.run(bot_token)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _pause_before_close()
        raise
