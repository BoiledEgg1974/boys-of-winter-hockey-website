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
player_master_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_master.csv'
player_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_skater_stats_rs.csv'
player_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_skater_stats_po.csv'
player_ratings_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_ratings.csv'
team_data_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/team_data.csv'
team_records_path = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/team_records.csv'
goalie_stats_path_rs = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_goalie_stats_rs.csv'
goalie_stats_path_po = 'C:/Users/keeno/OneDrive/Documents/Out of the Park Developments/Franchise Hockey Manager 11/saved_games/BOWL-Soft Cap.lg/import_export/csv/player_goalie_stats_po.csv'

# Seconds to reuse loaded CSV data across back-to-back commands (reduces disk I/O).
DATA_CACHE_TTL_SECONDS = 60.0
_df_cache = {}

# Optional: your server snowflake so channel names resolve unambiguously (recommended if the bot is in multiple servers).
# PowerShell: $env:DISCORD_GUILD_ID = "123456789012345678"
DISCORD_GUILD_ID = os.environ.get('DISCORD_GUILD_ID')

# command_key -> Discord text channel name (must match exactly, without #).
# help / stats are omitted so they stay usable from any channel (manual).
COMMAND_CHANNEL_NAMES = {
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
    'vezina': 'vezina',
    'goaliew': 'goalies-wins',
    'gl': 'goalies-losses',
    'gaa': 'goalies-gaa',
    'saves': 'goalies-saves',
    'svp': 'goalies-save-percentage',
    'so': 'goalies-shutouts',
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
    'powerrank': 'power-rankings',
    'prospectrank': 'prospect-rankings',
    'positionalrank': 'positional-rankings',
    'calder': 'calder-trophy',
}

# No automatic post on startup for these commands (channel slugs you listed).
NO_AUTOSTART_COMMAND_KEYS = frozenset({
    'standings',       # standings
    'gap',             # goals-assists-points
    'shots',           # shots
    'pminus',          # plus-minus
    'bs',              # blocked-shots
    'hits',            # hits
    'fights',          # fights
    'pim',             # penalty-minutes
    'ppg',             # power-play-goals
    'shg',             # shorthanded-goals
    'gwg',             # game-winning-goals
    'gva',             # giveaways
    'tka',             # takeaways
    'ovr',             # overall-game-rating
    'grd',             # defensive-game-rating
    'goaliew',         # goalies-wins
    'gl',              # goalies-losses
    'gaa',             # goalies-gaa
    'saves',           # goalies-saves
    'svp',             # goalies-save-percentage
    'so',              # goalies-shutouts
    'conn',            # conn-smythe
    'gro',             # offensive-game-rating
    'powerrank', 'prospectrank', 'positionalrank', 'calder',
})

# Full preference order; startup runs this minus NO_AUTOSTART_COMMAND_KEYS.
_ALL_COMMAND_KEYS_ORDER = [
    'standings', 'shots', 'gap', 'richard', 'norris', 'bourque', 'langway', 'selke',
    'ladybyng', 'artross', 'vezina', 'goaliew', 'gl', 'gaa', 'saves', 'svp', 'so',
    'conn', 'pminus', 'green', 'bs', 'hits', 'fights', 'pim', 'ppg', 'shg', 'gwg',
    'gva', 'tka', 'ovr', 'grd', 'gro',
    'powerrank', 'prospectrank', 'positionalrank', 'calder',
]

STARTUP_COMMAND_ORDER = [
    k for k in _ALL_COMMAND_KEYS_ORDER
    if k in COMMAND_CHANNEL_NAMES and k not in NO_AUTOSTART_COMMAND_KEYS
]

_resolved_command_channels = {}
_discord_event_poller_started = False
SITE_API_BASE_URL = os.environ.get('SITE_API_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
DISCORD_EVENTS_SHARED_SECRET = os.environ.get('DISCORD_EVENTS_SHARED_SECRET', 'bowluniverse').strip()
LEAGUE_SLUG = os.environ.get('LEAGUE_SLUG', 'bowl-cap').strip()
BOT_HEARTBEAT_NAME = os.environ.get('BOT_HEARTBEAT_NAME', 'bowl-cap-bot').strip()
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

team_emote_dict = {'MTL': '<:MTL:1333588537664213113>',
                   'TOR': '<:TOR:1333588859958591579>',
                   'BOS': '<:BOS:1429889226425634916>',
                   'CHI': '<:CHI:1333588196826812506>',
                   'DET': '<:DET:1333588258680078397>',
                   'NYR': '<:NYR:1333588602042454016>',
                   'LAK': '<:LAK:1485466999764156529>',
                   'CGY': '<:CGY:1429889471754539142>',
                   'NJD': '<:NJD:1383318334254092318>',
                   'STL': '<:STL:1485467024984379523>',
                   'BUF': '<:BUF:1449556416963809330>',
                   'NYI': '<:NYI:1468083975972196435>',
                   'OTT': '<:OTT:1377784222483484682>',
                   'TBL': '<:TBL:1377784481615708160>',
                   'PHI': '<:PHI:1333588627946471474>',
                   'PIT': '<:PIT:1383318357176221696>',
                   'ANA': '<:ANA:1398787454424842322>',
                   'FLA': '<:FLA:1398787440684171518>',
                   'VAN': '<:VAN:1468084057333170300>',
                   'WAS': '<:WSH:1429890537913188352>',
                   'PHX': '<:PHX:1449556427747364864>',
                   'EDM': '<:EDM:1449591412264796242>',
                   'CAR': '<:CAR:1468084009962573898>',
                   'COL': '<:COL:1429889654601158747>',
                   'DAL': '<:DAL:1398788096346161203>',
                   'NAS': '<:NSH:1470179048859767068>',
                   'SJS': '<:SJS:1360231209678016562>',}

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


def get_dataframes(playoffs=False):
    """Return cached dataframes from initialize_dataframes when fresh enough."""
    key = bool(playoffs)
    now = monotonic()
    ent = _df_cache.get(key)
    if ent and (now - ent[0]) < DATA_CACHE_TTL_SECONDS:
        return ent[1]
    data = initialize_dataframes(playoffs)
    _df_cache[key] = (now, data)
    return data


def _resolve_command_channels():
    """Fill _resolved_command_channels from COMMAND_CHANNEL_NAMES using guild text channel names."""
    global _resolved_command_channels
    _resolved_command_channels = {}
    guild = None
    gid = DISCORD_GUILD_ID
    if gid:
        guild = client.get_guild(int(gid))
        if guild is None:
            print(f'Could not find guild id {gid!r}; set DISCORD_GUILD_ID correctly.')
    elif len(client.guilds) == 1:
        guild = client.guilds[0]
    else:
        print(
            'Bot is in multiple guilds; set DISCORD_GUILD_ID in the environment so channel names can be resolved.'
        )
        return
    if guild is None:
        return
    by_name = {c.name: c.id for c in guild.text_channels}
    for cmd_key, ch_name in COMMAND_CHANNEL_NAMES.items():
        cid = by_name.get(ch_name)
        if cid is None:
            print(f'Warning: no text channel named {ch_name!r} for command {cmd_key!r}')
        else:
            _resolved_command_channels[cmd_key] = cid
    print(f'Resolved {len(_resolved_command_channels)}/{len(COMMAND_CHANNEL_NAMES)} command channels in {guild.name!r}.')


def command_allowed_in_channel(command_key, channel_id):
    if command_key not in COMMAND_CHANNEL_NAMES:
        return True
    expected = _resolved_command_channels.get(command_key)
    if expected is None:
        return False
    return channel_id == expected


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
    northeast_msg = '## Northeast Division\n'
    northeast_num = 0
    atlantic_msg = '## Atlantic Division\n'
    atlantic_num = 0
    southeast_msg = '## Southeast Division\n'
    southeast_num = 0
    central_msg = '## Central Division\n'
    central_num = 0
    pacific_msg = '## Pacific Division\n'
    pacific_num = 0
    northwest_msg = '## Northwest Division\n'
    northwest_num = 0
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
            if conferenceid == 0 and divisionid == 0: # northeast
                northeast_msg += (f"**{northeast_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                northeast_num += 1
            elif conferenceid == 0 and divisionid == 1: # atlantic
                atlantic_msg += (f"**{atlantic_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                atlantic_num += 1
            elif conferenceid == 0 and divisionid == 2: # southeast
                southeast_msg += (f"**{southeast_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                southeast_num += 1
            elif conferenceid == 1 and divisionid == 0: # central
                central_msg += (f"**{central_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                central_num += 1
            elif conferenceid == 1 and divisionid == 1: # pacific
                pacific_msg += (f"**{pacific_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                pacific_num += 1
            elif conferenceid == 1 and divisionid == 2: # northwest
                northwest_msg += (f"**{northwest_num+1}. {team_emote_dict[teamabbr]} {team}, {points} PTS**{gp} GP • {wins} W • {losses} L • {ties} T • {gf} GF • {ga} GA\n")
                northwest_num += 1
            
    final_message = (f">>> {northeast_msg}————————————————", f">>> {atlantic_msg}————————————————", f">>> {southeast_msg}————————————————", f">>> {central_msg}————————————————", f">>> {pacific_msg}————————————————", f">>> {northwest_msg}————————————————")
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
    message = ""
    for i, row in top_players_df.iterrows():
        player_name = f"{row['First Name']} {row['Last Name']}"
        tid = int(row['TeamId'])
        team_row = team_by_id.loc[tid]
        team = f"{team_row['Name']} {team_row['Nickname']}"
        goals = row['G']
        games_played = row['GP']
        points = row['Pts']
        shots_on_goal = row['SOG']
        goals_per_60 = row['GA/60']
        power_play_goals = row['PP G']
        offensive_game_rating = row['Game Rating Off']

        player_line = (f"**{i+1}. {team}, {player_name}, {goals}G**"
                       f"{games_played} GP • {points} Points • {shots_on_goal} SOG • "
                       f"{goals_per_60} GA/60 • {power_play_goals} PPG • {offensive_game_rating} OGR\n\n")
        message += player_line
    return message


async def send_skater_leaderboard(channel, playoffs, sort_column, ascending, header, formatter):
    player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = get_dataframes(playoffs)
    merged_df, goalie_merged_df = calculate_stats(
        player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df
    )
    sorted_df = sort_players_by_column(merged_df, sort_column, ascending)
    if sort_column not in sorted_df.columns:
        raise ValueError(f"Column `{sort_column}` not found.")
    sorted_df = sorted_df.reset_index(drop=True)
    body = formatter(sorted_df, team_data_df)
    await channel.send(header + body)


async def send_goalie_leaderboard(channel, playoffs, sort_column, ascending, header, formatter):
    player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = get_dataframes(playoffs)
    merged_df, goalie_merged_df = calculate_stats(
        player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df
    )
    sorted_df = sort_players_by_column(goalie_merged_df, sort_column, ascending)
    if sort_column not in sorted_df.columns:
        raise ValueError(f"Column `{sort_column}` not found.")
    sorted_df = sorted_df.reset_index(drop=True)
    body = formatter(sorted_df, team_data_df)
    await channel.send(header + body)


async def send_standings(channel):
    player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = get_dataframes(False)
    sorted_df = calculate_team_stats(team_data_df, team_records_df)
    parts = standings(sorted_df)
    await channel.send('## STANDINGS\n—————————————————\n')
    for division in parts:
        await channel.send(division)


async def send_stats_custom(channel, column_name):
    valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
    if column_name not in valid_columns:
        await channel.send("Invalid column name. Use `!help` to see valid columns.")
        return
    player_master_df, player_stats_df, player_ratings_df, goalie_stats_df, team_data_df, team_records_df = get_dataframes(False)
    merged_df, goalie_merged_df = calculate_stats(
        player_master_df, player_stats_df, team_data_df, player_ratings_df, goalie_stats_df
    )
    merged_df = sort_players_by_column(merged_df, column_name)
    if column_name not in merged_df.columns:
        raise ValueError(f"Column `{column_name}` not found.")
    top_players_df = merged_df.head(10).reset_index(drop=True)
    await channel.send(format_for_discord(top_players_df, team_data_df))


SKATER_LEADERBOARD_ROWS = [
    ('!shots', 'shots', 'SOG', False, '## SHOTS\n—————\n', shots),
    ('!gap', 'gap', 'Pts', False, '## GOALS • ASSISTS • POINTS\n———————————————————\n', goalsAssistsPoints),
    ('!richard', 'richard', 'G', False, '## <:RICH:1333588736235016264> RICHARD <:RICH:1333588736235016264>\n———————————\n', richard),
    ('!norris', 'norris', 'Pts', False, '## <:NORR:1345198959047479348> NORRIS <:NORR:1345198959047479348>\n——————————\n', norris),
    ('!bourque', 'bourque', 'G', False, '## <:BOUR:1333588068682301460> BOURQUE <:BOUR:1333588068682301460>\n————————————\n', bourque),
    ('!langway', 'langway', 'Game Rating Def', False, '## <:LANG:1333588445263560715> LANGWAY <:LANG:1333588445263560715>\n————————————\n', langway),
    ('!selke', 'selke', 'Game Rating Def', False, '## <:SELK:1333588794347225119> SELKE <:SELK:1333588794347225119>\n——————————\n', selke),
    ('!ladybyng', 'ladybyng', 'Pts Adjusted', False, '## <:BYNG:1333588125406199810> LADY BYNG <:BYNG:1333588125406199810>\n—————————————\n```PTS Adjusted = Points - PIM```\n', ladyByng),
    ('!artross', 'artross', 'Pts', False, '## <:ROSS:1333588774613028945> ART ROSS <:ROSS:1333588774613028945>\n————————————\n', goalsAssistsPoints),
    ('!pminus', 'pminus', '+/-', False, '## PLUS/MINUS\n——————————\n', plusMinus),
    ('!green', 'green', '+/-', True, '## <:GREEN:1333588297276198932> THE GREEN JACKET! <:GREEN:1333588297276198932> \n—————————————————————\n', plusMinus),
    ('!bs', 'bs', 'SB', False, '## BLOCKED SHOTS\n—————————————\n', blockedShots),
    ('!hits', 'hits', 'HIT', False, '## HITS\n————\n', hits),
    ('!fights', 'fights', 'Fights', False, '## FIGHTS\n——————\n', fights),
    ('!pim', 'pim', 'PIM', False, '## PENALTY MINUTES\n——————————————\n', penaltyMinutes),
    ('!ppg', 'ppg', 'PP G', False, '## POWER PLAY GOALS\n————————————————\n', powerPlayGoals),
    ('!shg', 'shg', 'SH G', False, '## SHORT HANDED GOALS\n——————————————————\n', shortHandedGoals),
    ('!gwg', 'gwg', 'GWG', False, '## GAME WINNING GOALS\n—————————————————\n', gameWinningGoals),
    ('!gva', 'gva', 'GvA', False, '## GIVEAWAYS\n—————————\n', giveaways),
    ('!tka', 'tka', 'TkA', False, '## TAKEAWAYS\n—————————\n', takeaways),
    ('!ovr', 'ovr', 'GR', False, '## OVERALL GAME RATING\n——————————————————\n', overallGameRating),
    ('!grd', 'grd', 'Game Rating Def', False, '## DEFENSIVE GAME RATING\n———————————————————\n', defensiveGameRating),
    ('!gro', 'gro', 'Game Rating Off', False, '## OFFENSIVE GAME RATING\n———————————————————\n', offensiveGameRating),
]

GOALIE_LEADERBOARD_ROWS = [
    ('!vezina', 'vezina', 'Wins', False, '## <:VEZI:1333588905999601684> VEZINA <:VEZI:1333588905999601684>\n——————————\n', vezina),
    ('!goaliew', 'goaliew', 'Wins', False, '## GOALIE WINS\n——————————\n', goalieWins),
    ('!gl', 'gl', 'Losses', False, '## GOALIE LOSSES\n————————————\n', goalieLosses),
    ('!gaa', 'gaa', 'Goals Against Average', True, '## GOALS AGAINST AVERAGE\n———————————————————\n', goalieGAA),
    ('!saves', 'saves', 'Saves', False, '## SAVES\n——————\n', goalieSaves),
    ('!svp', 'svp', 'Save Percentage', False, '## SAVE PERCENTAGE\n——————————————\n', goalieSavePercentage),
    ('!so', 'so', 'Shutouts', False, '## SHUTOUTS\n—————————\n', goalieShutouts),
]


def _skater_cmd_runner(sort_column, ascending, header, formatter):
    async def runner(channel):
        await send_skater_leaderboard(channel, False, sort_column, ascending, header, formatter)
    return runner


def _goalie_cmd_runner(sort_column, ascending, header, formatter):
    async def runner(channel):
        await send_goalie_leaderboard(channel, False, sort_column, ascending, header, formatter)
    return runner


def _build_command_routes():
    routes = []
    for prefix, key, sort_column, ascending, header, formatter in SKATER_LEADERBOARD_ROWS:
        routes.append((prefix, key, _skater_cmd_runner(sort_column, ascending, header, formatter)))
    for prefix, key, sort_column, ascending, header, formatter in GOALIE_LEADERBOARD_ROWS:
        routes.append((prefix, key, _goalie_cmd_runner(sort_column, ascending, header, formatter)))

    async def run_standings(channel):
        await send_standings(channel)

    routes.append(('!standings', 'standings', run_standings))

    async def run_conn(channel):
        await send_skater_leaderboard(
            channel,
            True,
            'Pts',
            False,
            '## <:CONN:1333588217286492321> CONN SMYTHE <:CONN:1333588217286492321> \n————————————————\n',
            goalsAssistsPoints,
        )
        await send_goalie_leaderboard(
            channel,
            True,
            'Wins',
            False,
            '## GOALIES BY WINS\n—————————————\n',
            vezina,
        )

    routes.append(('!conn', 'conn', run_conn))

    async def run_site_feed_stub(channel, title: str):
        await channel.send(
            f'**{title}**\n'
            'This channel receives notifications when the BOWL website queues a Discord event. '
            'FHM CSV `!` output for this board is not implemented in the bot yet.'
        )

    async def run_powerrank(ch):
        await run_site_feed_stub(ch, 'Power rankings')

    async def run_prospectrank(ch):
        await run_site_feed_stub(ch, 'Prospect rankings')

    async def run_positionalrank(ch):
        await run_site_feed_stub(ch, 'Positional rankings')

    async def run_calder(ch):
        await run_site_feed_stub(ch, 'Calder / rookie spotlight')

    routes.append(('!powerrank', 'powerrank', run_powerrank))
    routes.append(('!prospectrank', 'prospectrank', run_prospectrank))
    routes.append(('!positionalrank', 'positionalrank', run_positionalrank))
    routes.append(('!calder', 'calder', run_calder))
    return routes


COMMAND_ROUTES = _build_command_routes()


async def run_command_by_key(channel, command_key):
    for prefix, key, runner in COMMAND_ROUTES:
        if key == command_key:
            await runner(channel)
            return


@client.event
async def on_ready():
    global _discord_event_poller_started
    print(f'Logged in as {client.user}')
    _resolve_command_channels()
    for command_key in STARTUP_COMMAND_ORDER:
        cid = _resolved_command_channels.get(command_key)
        if cid is None:
            continue
        ch = client.get_channel(cid)
        if ch is None:
            print(f'Startup: could not load channel id {cid} for {command_key!r}')
            continue
        try:
            await run_command_by_key(ch, command_key)
        except Exception as exc:
            print(f'Startup post failed for {command_key!r} -> {cid}: {exc}')
    if DISCORD_EVENTS_SHARED_SECRET and not _discord_event_poller_started:
        _discord_event_poller_started = True
        client.loop.create_task(discord_event_poller())


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if not message.content.startswith('!'):
        return

    content = message.content.strip()

    if content.startswith('!help'):
        if not command_allowed_in_channel('help', message.channel.id):
            return
        valid_columns = ['G', 'GP', 'Pts', 'SOG', 'GA/60', 'PP G', 'Game Rating Off']
        help_message = (
            'Here are the valid columns you can sort by:\n'
            + ', '.join(valid_columns)
            + '\nUse them with the !stats command. Example: `!stats,Game Rating Off`'
        )
        await message.channel.send(help_message)
        return

    if content.startswith('!stats'):
        if not command_allowed_in_channel('stats', message.channel.id):
            return
        try:
            parts = content.split(',', 1)
            if len(parts) < 2:
                await message.channel.send('Usage: `!stats,<column>` — see `!help` for columns.')
                return
            column_name = parts[1].strip()
            await send_stats_custom(message.channel, column_name)
        except Exception as e:
            await message.channel.send(f'An error occurred: {str(e)}')
        return

    token = content.split(None, 1)[0]
    for prefix, key, runner in COMMAND_ROUTES:
        if token == prefix:
            if not command_allowed_in_channel(key, message.channel.id):
                return
            try:
                await runner(message.channel)
            except Exception as e:
                await message.channel.send(f'An error occurred: {str(e)}')
            return


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
