import asyncio
import json
import os
import sys
from pathlib import Path
from time import monotonic

import discord
import pandas as pd
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
player_master_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_master.csv'
player_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_skater_stats_rs.csv'
player_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_skater_stats_po.csv'
player_ratings_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_ratings.csv'
team_data_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/team_data.csv'
team_records_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/team_records.csv'
goalie_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_goalie_stats_rs.csv'
goalie_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Fantasy.lg/import_export/csv/player_goalie_stats_po.csv'

team_emote_dict = {'IND': '<:IND:1250473797685870602>',
                   'KUN': '<:KUN:1463263903902334976>',
                   'EDM': '<:EDM:1207502268354662410>',
                   'MTL': '<:MTL:1405351657314979951>',
                   'POR': '<:POR:1472096312324522109>',
                   'TOR': '<:TOR:1252375053144686633>',
                   'SIX': '<:SIX:1373118421478281297>',
                   'WIC': '<:WIC:1236501118864068731>',
                   'FLA': '<:FLA:1472096060842180649>',
                   'MON': '<:MON:1373118140996915255>',
                   'FW': '<:FW:1490064700170571776>',
                   'CAN': '<:CAN:1486766929254285383>',
                   'HAL': '<:HAL:1341868533881241631>',
                   'TRL': '<:TRL:1277415927889264681>',
                   'LON': '<:LON:1458639710170644510>',
                   'ME': '<:ME:1472096110389493965>',
                   'VCR': '<:VCR:1472096172859461697>',
                   'TOK': '<:TOK:1388762046220210246>',
                   'CHI': '<:CHI:1207501451966816306>',
                   'BGK': '<:BGK:1373117910989668453>',
                   'VIC': '<:VIC:1420938447958311013>',
                   'KEN': '<:KEN:1383317330339041290>',
                   'PIT': '<:PIT:1388761974443081868>',
                   'HAM': '<:HAM:1453221895775453327>'}

# ---------------------------------------------------------------------------
# Commands → Discord channel *names* (must match exactly; no # prefix).
# Resolved to channel IDs when the bot connects. Optional DISCORD_GUILD_ID env
# if the bot is in multiple servers (otherwise the first guild is used).
# help / stats: manual anywhere — not listed here.
COMMAND_CHANNEL_BY_NAME = {
    'standings': 'standings',
    'shots': 'shots',
    'gap': 'goals-assists-points',
    'richard': 'richard',
    'norris': 'norris',
    'bourque': 'bourque',
    'langway': 'langway',
    'selke': 'selke',
    'ladybyng': 'lady-byng',
    'artross': 'art-ross',
    'conn': 'conn-smythe',
    'pminus': 'plus-minus',
    'green': 'green-jacket',
    'bs': 'blocked-shots',
    'hits': 'hits',
    'fights': 'fights',
    'pim': 'penalty-minutes',
    'ppg': 'power-play-goals',
    'shg': 'shorthanded-goals',
    'gwg': 'game-winning-goals',
    'gva': 'giveaways',
    'tka': 'takeaways',
    'ovr': 'overall-game-rating',
    'grd': 'defensive-game-rating',
    'gro': 'offensive-game-rating',
    'vezina': 'vezina',
    'goaliew': 'goalies-wins',
    'gl': 'goalies-losses',
    'gaa': 'goalies-gaa',
    'saves': 'goalies-saves',
    'svp': 'goalies-save-percentage',
    'so': 'goalies-shutouts',
    'powerrank': 'power-rankings',
    'prospectrank': 'prospect-rankings',
    'positionalrank': 'positional-rankings',
    'calder': 'calder-trophy',
}

# Skip startup auto-post for these command keys (manual !commands in those channels still work).
_AUTORUN_SKIP_COMMAND_KEYS = frozenset({
    'standings', 'conn', 'gap', 'shots', 'pminus', 'bs', 'hits', 'fights', 'pim',
    'ppg', 'shg', 'gwg', 'gva', 'tka', 'ovr', 'grd', 'gro',
    'goaliew', 'gl', 'gaa', 'saves', 'svp', 'so',
    'powerrank', 'prospectrank', 'positionalrank', 'calder',
})

# Order for startup posts (subset of COMMAND_CHANNEL_BY_NAME; help/stats never listed).
AUTORUN_COMMAND_ORDER = tuple(
    k for k in COMMAND_CHANNEL_BY_NAME if k not in _AUTORUN_SKIP_COMMAND_KEYS
)

# Post every mapped command to its channel once when the bot starts.
AUTORUN_ALL_MAPPED_COMMANDS_ON_STARTUP = True

# Optional override: map command key → list of allowed numeric channel IDs (wins
# over name lookup). Use for unusual setups.
COMMAND_CHANNEL_ALLOWLIST = {}

# Filled in on_ready:
_RESOLVED_CHANNEL_ALLOWLIST = {}
_UNRESOLVED_RESTRICTED_COMMANDS = frozenset()
_STARTUP_JOBS = []
_autorun_once_per_process = False
_discord_event_poller_started = False
SITE_API_BASE_URL = os.environ.get('SITE_API_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
DISCORD_EVENTS_SHARED_SECRET = os.environ.get('DISCORD_EVENTS_SHARED_SECRET', 'bowluniverse').strip()
LEAGUE_SLUG = os.environ.get('LEAGUE_SLUG', 'bowl-fantasy').strip()
DISCORD_GUILD_ID = os.environ.get('DISCORD_GUILD_ID', '').strip()
BOT_HEARTBEAT_NAME = os.environ.get('BOT_HEARTBEAT_NAME', 'bowl-fantasy-bot').strip()
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
    fo_safe = merged_df['FO'].replace(0, float('nan'))
    pct = merged_df['FOW'] / fo_safe * 100
    merged_df['FO%'] = pct.round(1).apply(lambda x: f'{x:.1f}%' if pd.notna(x) else '0.0%')
    return merged_df, goalie_merged_df

def calculate_team_stats(team_data_df, team_records_df):
    team_records_df_column_change = team_records_df.rename(columns={'Team Id': 'TeamId'})
    merged_df = pd.merge(team_data_df, team_records_df_column_change, on='TeamId')
    merged_df['GP'] = merged_df['Wins'] + merged_df['Losses'] + merged_df['Ties']
    sorted_df = sort_players_by_column(merged_df, 'Points')
    return sorted_df
    # WRITE THE STANDINGS FUNCTION WITH THE MERGED DATAFRAMES

def standings(sorted_df):
    adams_msg = '## Adams Division\n'
    adams_num = 0
    patrick_msg = '## Patrick Division\n'
    patrick_num = 0
    norris_msg = '## Norris Division\n'
    norris_num = 0
    smythe_msg = '## Smythe Division\n'
    smythe_num = 0
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
            if conferenceid == 0 and divisionid == 0: # adams
                adams_msg += (f"**{adams_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                adams_num += 1
            elif conferenceid == 0 and divisionid == 1: # norris
                norris_msg += (f"**{norris_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                norris_num += 1
            elif conferenceid == 1 and divisionid == 0: # patrick
                patrick_msg += (f"**{patrick_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                patrick_num += 1
            elif conferenceid == 1 and divisionid == 1: # smythe
                smythe_msg += (f"**{smythe_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                smythe_num += 1
            
    final_message = (f">>> {adams_msg}————————————————", f">>> {norris_msg}————————————————", f">>> {patrick_msg}————————————————", f">>> {smythe_msg}————————————————")
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
    team_by_id = team_data_df.set_index('TeamId')
    message = ''
    for i, row in top_players_df.iterrows():
        player_name = f"{row['First Name']} {row['Last Name']}"
        tr = team_by_id.loc[row['TeamId']]
        team = f"{tr['Name']} {tr['Nickname']}"
        goals = row['G']
        games_played = row['GP']
        points = row['Pts']
        shots_on_goal = row['SOG']
        goals_per_60 = row['GA/60']
        power_play_goals = row['PP G']
        offensive_game_rating = row['Game Rating Off']
        player_line = (
            f"**{i + 1}. {team}, {player_name}, {goals}G**"
            f"{games_played} GP • {points} Points • {shots_on_goal} SOG • "
            f"{goals_per_60} GA/60 • {power_play_goals} PPG • {offensive_game_rating} OGR\n\n"
        )
        message += player_line
    return message


def load_fantasy_context(playoffs=False):
    player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = (
        initialize_dataframes(playoffs)
    )
    merged_df, goalie_merged_df = calculate_stats(
        player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df
    )
    return {
        'merged_df': merged_df,
        'goalie_merged_df': goalie_merged_df,
        'team_data_df': team_data_df,
        'team_records_df': team_records_df,
    }


_CMD_ORDER = (
    'positionalrank', 'prospectrank', 'powerrank',
    'standings', 'ladybyng', 'goaliew', 'artross', 'bourque', 'langway',
    'richard', 'conn', 'pminus', 'fights', 'norris', 'vezina', 'selke',
    'green', 'saves', 'shots', 'help', 'hits',
    'gap', 'gaa', 'gwg', 'gva', 'tka', 'ovr', 'grd', 'gro', 'svp', 'ppg', 'shg', 'pim',
    'gl', 'so', 'bs', 'calder',
)


def parse_command_key(content: str):
    content = content.strip()
    if not content.startswith('!'):
        return None
    if content.startswith('!stats,'):
        return 'stats'
    body = content[1:]
    for name in _CMD_ORDER:
        if body == name or body.startswith(name + ' '):
            return name
    return None


def setup_channel_resolution_and_startup_queue():
    """Match COMMAND_CHANNEL_BY_NAME to guild text channels; build allowlist + startup queue."""
    global _RESOLVED_CHANNEL_ALLOWLIST, _UNRESOLVED_RESTRICTED_COMMANDS, _STARTUP_JOBS
    _RESOLVED_CHANNEL_ALLOWLIST = {}
    missing = set()

    guild = None
    raw_gid = os.environ.get('DISCORD_GUILD_ID')
    if raw_gid and raw_gid.isdigit():
        guild = discord.utils.get(client.guilds, id=int(raw_gid))
        if guild is None:
            print(f'DISCORD_GUILD_ID={raw_gid} not in visible guilds yet')
    if guild is None and len(client.guilds) == 1:
        guild = client.guilds[0]
    elif guild is None and client.guilds:
        guild = client.guilds[0]
        print(
            f'Multiple guilds ({len(client.guilds)}); using "{guild.name}". '
            'Set DISCORD_GUILD_ID to pick a specific server.'
        )

    if guild is None:
        print('No guild available for channel name resolution.')
        _UNRESOLVED_RESTRICTED_COMMANDS = frozenset(COMMAND_CHANNEL_BY_NAME.keys())
        _STARTUP_JOBS = []
        return

    name_to_channel = {}
    for tc in guild.text_channels:
        name_to_channel.setdefault(tc.name, tc)

    for cmd, ch_name in COMMAND_CHANNEL_BY_NAME.items():
        ch = name_to_channel.get(ch_name)
        if ch is not None:
            _RESOLVED_CHANNEL_ALLOWLIST[cmd] = [ch.id]
        else:
            missing.add(cmd)
            print(f'Channel "#{ch_name}" not found for !{cmd} (guild "{guild.name}")')

    _UNRESOLVED_RESTRICTED_COMMANDS = frozenset(missing)

    _STARTUP_JOBS = []
    if AUTORUN_ALL_MAPPED_COMMANDS_ON_STARTUP:
        for cmd in AUTORUN_COMMAND_ORDER:
            ids = _RESOLVED_CHANNEL_ALLOWLIST.get(cmd)
            if ids:
                _STARTUP_JOBS.append((ids[0], f'!{cmd}'))


def channel_allowed(cmd_key: str, channel_id: int, internal: bool) -> bool:
    if internal:
        return True
    if cmd_key in {'help', 'stats'}:
        return True
    manual = COMMAND_CHANNEL_ALLOWLIST.get(cmd_key)
    if manual is not None and len(manual) > 0:
        return channel_id in manual
    if cmd_key in _UNRESOLVED_RESTRICTED_COMMANDS:
        return False
    allowed_ids = _RESOLVED_CHANNEL_ALLOWLIST.get(cmd_key)
    if allowed_ids is None:
        return True
    return channel_id in allowed_ids


# (playoffs, sort_column, ascending, header, formatter)
SKATER_SPECS = {
    'shots': (False, 'SOG', False, '## SHOTS\n—————\n', shots),
    'gap': (False, 'Pts', False, '## GOALS • ASSISTS • POINTS\n———————————————————\n', goalsAssistsPoints),
    'richard': (False, 'G', False, '## <:RICH:1207522600276987935> RICHARD <:RICH:1207522600276987935>\n———————————\n', richard),
    'norris': (False, 'Pts', False, '## <:NORR:1207522368138772520> NORRIS <:NORR:1207522368138772520>\n——————————\n', norris),
    'bourque': (False, 'G', False, '## <:BOUR:1212579333714087966> BOURQUE <:BOUR:1212579333714087966>\n————————————\n', bourque),
    'langway': (False, 'Game Rating Def', False, '## <:LANG:1212580107508654120> LANGWAY <:LANG:1212580107508654120>\n————————————\n', langway),
    'selke': (False, 'Game Rating Def', False, '## <:SELK:1207525050908156024> SELKE <:SELK:1207525050908156024>\n——————————\n', selke),
    'ladybyng': (False, 'Pts Adjusted', False, '## <:BYNG:1207524330754285589> LADY BYNG <:BYNG:1207524330754285589>\n—————————————\n```PTS Adjusted = Points - PIM```\n', ladyByng),
    'artross': (False, 'Pts', False, '## <:ROSS:1207524627727917076> ART ROSS <:ROSS:1207524627727917076>\n————————————\n', goalsAssistsPoints),
    'pminus': (False, '+/-', False, '## PLUS/MINUS\n——————————\n', plusMinus),
    'green': (False, '+/-', True, '## <:GREEN:1230239053698568397> THE GREEN JACKET! <:GREEN:1230239053698568397> \n—————————————————————\n', plusMinus),
    'bs': (False, 'SB', False, '## BLOCKED SHOTS\n—————————————\n', blockedShots),
    'hits': (False, 'HIT', False, '## HITS\n————\n', hits),
    'fights': (False, 'Fights', False, '## FIGHTS\n——————\n', fights),
    'pim': (False, 'PIM', False, '## PENALTY MINUTES\n——————————————\n', penaltyMinutes),
    'ppg': (False, 'PP G', False, '## POWER PLAY GOALS\n————————————————\n', powerPlayGoals),
    'shg': (False, 'SH G', False, '## SHORT HANDED GOALS\n——————————————————\n', shortHandedGoals),
    'gwg': (False, 'GWG', False, '## GAME WINNING GOALS\n—————————————————\n', gameWinningGoals),
    'gva': (False, 'GvA', False, '## GIVEAWAYS\n—————————\n', giveaways),
    'tka': (False, 'TkA', False, '## TAKEAWAYS\n—————————\n', takeaways),
    'ovr': (False, 'GR', False, '## OVERALL GAME RATING\n——————————————————\n', overallGameRating),
    'grd': (False, 'Game Rating Def', False, '## DEFENSIVE GAME RATING\n———————————————————\n', defensiveGameRating),
    'gro': (False, 'Game Rating Off', False, '## OFFENSIVE GAME RATING\n———————————————————\n', offensiveGameRating),
}

GOALIE_SPECS = {
    'vezina': (False, 'Wins', False, '## <:VEZI:1207525828871848017> VEZINA <:VEZI:1207525828871848017>\n——————————\n', vezina),
    'goaliew': (False, 'Wins', False, '## GOALIE WINS\n——————————\n', goalieWins),
    'gl': (False, 'Losses', False, '## GOALIE LOSSES\n————————————\n', goalieLosses),
    'gaa': (False, 'Goals Against Average', True, '## GOALS AGAINST AVERAGE\n———————————————————\n', goalieGAA),
    'saves': (False, 'Saves', False, '## SAVES\n——————\n', goalieSaves),
    'svp': (False, 'Save Percentage', False, '## SAVE PERCENTAGE\n——————————————\n', goalieSavePercentage),
    'so': (False, 'Shutouts', False, '## SHUTOUTS\n—————————\n', goalieShutouts),
}


class _StubMessage:
    __slots__ = ('channel', 'content', 'author')

    def __init__(self, channel, content):
        self.channel = channel
        self.content = content
        self.author = None


async def dispatch_command(message, cmd_key: str, content: str, internal: bool):
    ch = message.channel
    cid = ch.id

    async def deny():
        await ch.send('That command is restricted to other channels in this server.')

    try:
        if cmd_key == 'help':
            if not channel_allowed('help', cid, internal):
                await deny()
                return
            valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
            help_message = (
                'Here are the valid columns you can sort by:\n'
                + ', '.join(valid_columns)
                + '\nUse them with the !stats command. Example: `!stats,Game Rating Off`'
            )
            await ch.send(help_message)
            return

        if cmd_key == 'stats':
            if not channel_allowed('stats', cid, internal):
                await deny()
                return
            parts = content.split(',', 1)
            if len(parts) < 2:
                await ch.send('Use `!stats,<column>` with a valid column. See `!help`.')
                return
            column_name = parts[1].strip()
            valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
            if column_name not in valid_columns:
                await ch.send('Invalid column name. Use `!help` to see valid columns.')
                return
            ctx = load_fantasy_context(False)
            merged_df = ctx['merged_df']
            if column_name not in merged_df.columns:
                raise ValueError(f'Column `{column_name}` not found.')
            sorted_df = sort_players_by_column(merged_df, column_name).head(10).reset_index(drop=True)
            await ch.send(format_for_discord(sorted_df, ctx['team_data_df']))
            return

        if cmd_key == 'standings':
            if not channel_allowed('standings', cid, internal):
                await deny()
                return
            ctx = load_fantasy_context(False)
            sorted_df = calculate_team_stats(ctx['team_data_df'], ctx['team_records_df'])
            parts = standings(sorted_df)
            await ch.send('## STANDINGS\n—————————————————\n')
            for i in range(4):
                await ch.send(parts[i])
            return

        if cmd_key in {'powerrank', 'prospectrank', 'positionalrank', 'calder'}:
            if not channel_allowed(cmd_key, cid, internal):
                await deny()
                return
            await ch.send(
                '**BOWL website feed**\n'
                'This channel receives notifications when the site queues a Discord event. '
                'CSV-based `!` output for this board is not implemented in the bot yet — use the website or admin test queue.'
            )
            return

        if cmd_key == 'conn':
            if not channel_allowed('conn', cid, internal):
                await deny()
                return
            ctx = load_fantasy_context(True)
            sk = sort_players_by_column(ctx['merged_df'], 'Pts').reset_index(drop=True)
            await ch.send(
                '## <:CONN:1207525329825173524> CONN SMYTHE <:CONN:1207525329825173524> \n————————————————\n'
                + goalsAssistsPoints(sk, ctx['team_data_df'])
            )
            gk = sort_players_by_column(ctx['goalie_merged_df'], 'Wins').reset_index(drop=True)
            await ch.send(
                '## GOALIES BY WINS\n—————————————\n' + vezina(gk, ctx['team_data_df'])
            )
            return

        spec = SKATER_SPECS.get(cmd_key)
        if spec is not None:
            if not channel_allowed(cmd_key, cid, internal):
                await deny()
                return
            playoffs, col, asc, hdr, fmt_fn = spec
            ctx = load_fantasy_context(playoffs)
            sorted_df = sort_players_by_column(ctx['merged_df'], col, asc).reset_index(drop=True)
            if col not in sorted_df.columns:
                raise ValueError(f'Column `{col}` not found.')
            await ch.send(hdr + fmt_fn(sorted_df, ctx['team_data_df']))
            return

        specg = GOALIE_SPECS.get(cmd_key)
        if specg is not None:
            if not channel_allowed(cmd_key, cid, internal):
                await deny()
                return
            playoffs, col, asc, hdr, fmt_fn = specg
            ctx = load_fantasy_context(playoffs)
            sorted_df = sort_players_by_column(ctx['goalie_merged_df'], col, asc).reset_index(drop=True)
            if col not in sorted_df.columns:
                raise ValueError(f'Column `{col}` not found.')
            await ch.send(hdr + fmt_fn(sorted_df, ctx['team_data_df']))
            return

    except Exception as e:
        await ch.send(f'An error occurred: {str(e)}')


async def handle_incoming_message(message, *, internal: bool = False):
    if not internal:
        if message.author == client.user:
            return
        if getattr(message.author, 'bot', False):
            return
    content = (message.content or '').strip()
    if not content.startswith('!'):
        return
    cmd_key = parse_command_key(content)
    if cmd_key is None:
        return
    await dispatch_command(message, cmd_key, content, internal)


async def run_startup_autorun():
    if not _STARTUP_JOBS:
        return
    await asyncio.sleep(2)
    for channel_id, payload in _STARTUP_JOBS:
        channel = client.get_channel(channel_id)
        if channel is None:
            print(f'Startup autorun: channel {channel_id} not found')
            continue
        await handle_incoming_message(_StubMessage(channel, payload), internal=True)


@client.event
async def on_ready():
    global _autorun_once_per_process, _discord_event_poller_started
    print(f'Logged in as {client.user} (id={client.user.id})')
    setup_channel_resolution_and_startup_queue()
    if AUTORUN_ALL_MAPPED_COMMANDS_ON_STARTUP and not _autorun_once_per_process:
        _autorun_once_per_process = True
        client.loop.create_task(run_startup_autorun())
    if DISCORD_EVENTS_SHARED_SECRET and not _discord_event_poller_started:
        _discord_event_poller_started = True
        client.loop.create_task(discord_event_poller())


@client.event
async def on_message(message):
    await handle_incoming_message(message, internal=False)


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


def _channel_for_event_key(channel_key: str):
    want = EVENT_CHANNEL_BY_KEY.get(str(channel_key or '').strip(), str(channel_key or '').strip())
    if not want:
        return None
    for g in client.guilds:
        for ch in g.text_channels:
            if (ch.name or '').casefold() == want.casefold():
                return ch
    return None


async def _deliver_event_to_channel(ev: dict) -> None:
    ch = _channel_for_event_key(str(ev.get('channel_key') or ''))
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
