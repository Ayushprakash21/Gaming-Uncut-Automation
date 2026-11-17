"""
Extensible Game Knowledge Layer for Gaming Uncut Automation.
Maintains visual signatures, UI/HUD traits, key weapons, vehicles, and genre classifications
for popular gaming franchises to verify multimodal video recognition and prevent hallucinations.
"""

from typing import Dict, Any, List, Optional
import re

GAME_REGISTRY: Dict[str, Dict[str, Any]] = {
    "Grand Theft Auto V": {
        "franchise": "Grand Theft Auto",
        "genre": "Open World Action-Adventure",
        "developer": "Rockstar Games",
        "aliases": ["GTA 5", "GTA V", "GTA Online", "Grand Theft Auto 5", "gta5", "gtav"],
        "hud_elements": [
            "circular or rectangular radar mini-map bottom-left",
            "health and armor bars below mini-map",
            "weapon wheel selector",
            "five wanted stars top-right",
            "money counter top-right with dollar sign",
            "ammo counter top-right"
        ],
        "key_weapons": ["RPG", "Homing Launcher", "Minigun", "Heavy Sniper", "Sticky Bomb", "Special Carbine", "AP Pistol"],
        "key_vehicles": ["Oppressor Mk II", "Zentorno", "Buzzard", "Hydra", "Police Cruiser", "Deluxo", "Kuruma"],
        "locations": ["Los Santos", "Vinewood", "Blaine County", "Sandy Shores", "Mount Chiliad", "Paleto Bay"],
        "visual_style": "Third-person or first-person modern Californian urban and desert open world with realistic lighting"
    },
    "Grand Theft Auto VI": {
        "franchise": "Grand Theft Auto",
        "genre": "Open World Action-Adventure",
        "developer": "Rockstar Games",
        "aliases": ["GTA 6", "GTA VI", "Grand Theft Auto 6"],
        "hud_elements": ["Vice City police system", "modernized mini-map", "weapon wheel"],
        "key_weapons": ["Assault Rifle", "Pistol", "Shotgun"],
        "key_vehicles": ["Airboat", "Muscle cars", "Speedboat"],
        "locations": ["Leonida", "Vice City", "Port Gellhorn", "Kelly County", "Keys"],
        "visual_style": "Next-gen neon tropical Florida-inspired open world"
    },
    "Red Dead Redemption 2": {
        "franchise": "Red Dead",
        "genre": "Open World Western Action-Adventure",
        "developer": "Rockstar Games",
        "aliases": ["RDR2", "RDR 2", "Red Dead 2", "Red Dead Redemption 2"],
        "hud_elements": [
            "circular compass mini-map bottom-left",
            "inner Dead Eye, health, and stamina core meters",
            "weapon wheel with horse cargo tab",
            "honor meter prompt"
        ],
        "key_weapons": ["Cattleman Revolver", "Lancaster Repeater", "Double-barreled Shotgun", "Bow and Arrow", "Dynamite"],
        "key_vehicles": ["Arabian Horse", "Thoroughbred", "Stagecoach", "Steam Train"],
        "locations": ["Valentine", "Saint Denis", "Strawberry", "Blackwater", "Rhodes", "Ambarino", "Grizzlies"],
        "visual_style": "Hyper-realistic late 1800s American frontier, natural wilderness, horseback riding"
    },
    "Black Myth: Wukong": {
        "franchise": "Black Myth",
        "genre": "Action RPG / Soulslike",
        "developer": "Game Science",
        "aliases": ["Black Myth Wukong", "Wukong", "Black Myth", "BMW"],
        "hud_elements": [
            "health and mana bars top-left or bottom-center",
            "curved stamina meter",
            "transformation and spirit vessel icons bottom-right",
            "gourd drink counter bottom-right",
            "focus point pips around staff stance indicator"
        ],
        "key_weapons": ["Staff", "Ruyi Jingu Bang", "Spear", "Spells", "A Pluck of Many"],
        "characters": ["Destined One", "Sun Wukong", "Tiger Vanguard", "Black Bear Guai", "Yellow Wind Sage", "Erlang Shen"],
        "locations": ["Black Wind Mountain", "Yellow Wind Ridge", "Pagoda Realm", "Flaming Mountains"],
        "visual_style": "Unreal Engine 5 hyper-detailed mythological ancient China, acrobatic staff martial arts combat"
    },
    "Assassin's Creed Shadows": {
        "franchise": "Assassin's Creed",
        "genre": "Action RPG / Stealth",
        "developer": "Ubisoft",
        "aliases": ["AC Shadows", "Assassins Creed Shadows", "AC Japan"],
        "hud_elements": [
            "compass navigation bar top of screen",
            "health and stance posture meters",
            "stealth detection indicator",
            "dual protagonist switch prompt"
        ],
        "key_weapons": ["Katana", "Kusarigama", "Shuriken", "Teppo Matchlock", "Kanabo Club"],
        "characters": ["Yasuke", "Naoe", "Oda Nobunaga"],
        "locations": ["Kyoto", "Osaka", "Azuchi Castle", "Iga", "Feudal Japan"],
        "visual_style": "Feudal Sengoku-era Japan, dynamic seasonal weather, ninja parkour and samurai heavy combat"
    },
    "Call of Duty: Warzone": {
        "franchise": "Call of Duty",
        "genre": "First-Person Shooter / Battle Royale",
        "developer": "Activision",
        "aliases": ["Warzone", "COD Warzone", "Warzone 2", "MW3 Warzone", "Call of Duty Warzone"],
        "hud_elements": [
            "tactical compass bar top center",
            "minimap top-left with red enemy ping dots",
            "three-segmented armor plate health bar bottom-center",
            "ammo counter and weapon attachment icons bottom-right",
            "kill feed with weapon icons top-right",
            "Gulag countdown banner"
        ],
        "key_weapons": ["Kar98k", "MCW", "HRM-9", "Superi 46", "Kastov 762", "Sniper Rifle", "Riot Shield"],
        "locations": ["Verdansk", "Urzikstan", "Rebirth Island", "Fortune's Keep", "Vondel"],
        "visual_style": "Fast-paced first-person tactical military combat, sprint slide-cancels, parachute drops"
    },
    "Counter-Strike 2": {
        "franchise": "Counter-Strike",
        "genre": "Tactical First-Person Shooter",
        "developer": "Valve",
        "aliases": ["CS2", "Counter Strike 2", "CS:GO", "Counter-Strike: Global Offensive"],
        "hud_elements": [
            "five vs five player avatar card bar top center",
            "round time and bomb timer top center",
            "clean radar minimap top-left",
            "health numerical display bottom-left",
            "ammo and magazine count bottom-right",
            "Source 2 volumetric smoke clouds"
        ],
        "key_weapons": ["AK-47", "M4A4", "M4A1-S", "AWP", "Desert Eagle", "USP-S", "Glock-18", "Karambit"],
        "locations": ["Dust II", "Mirage", "Inferno", "Nuke", "Overpass", "Ancient", "Anubis"],
        "visual_style": "Ultra-precise first-person competitive tactical shooting, crosshair placement, bomb defusal"
    },
    "Valorant": {
        "franchise": "Valorant",
        "genre": "Tactical Hero Shooter",
        "developer": "Riot Games",
        "aliases": ["Val", "Valorant"],
        "hud_elements": [
            "ten agent character portraits top center bar",
            "agent ability icons and ultimate points bottom center",
            "health and shield numbers bottom-left",
            "weapon ammunition and crosshair bottom-right",
            "spike plant alert banner"
        ],
        "key_weapons": ["Vandal", "Phantom", "Operator", "Sheriff", "Spectre", "Ghost", "Odin"],
        "characters": ["Jett", "Reyna", "Raze", "Omen", "Sova", "Clove", "Chamber", "Sage"],
        "locations": ["Bind", "Haven", "Ascent", "Split", "Breeze", "Lotus", "Sunset", "Abyss"],
        "visual_style": "Stylized cell-shaded tactical hero FPS with colorful magical and technological ability VFX"
    },
    "PUBG: Battlegrounds": {
        "franchise": "PUBG",
        "genre": "Battle Royale",
        "developer": "Krafton",
        "aliases": ["PUBG", "PlayerUnknowns Battlegrounds", "PUBG PC"],
        "hud_elements": [
            "compass bar with numerical degrees at top",
            "alive player counter and kill count top-right",
            "blue zone closing timer bottom-right above map",
            "helmet, vest, and backpack durability icons bottom center"
        ],
        "key_weapons": ["M416", "Beryl M762", "AWM", "Kar98k", "M24", "Pan", "Groza"],
        "key_vehicles": ["UAZ", "Dacia", "Buggy", "Motorcycle", "BRDM-2"],
        "locations": ["Erangel", "Miramar", "Taego", "Vikendi", "Rondo", "Deston", "Sanhok"],
        "visual_style": "Realistic tactical third-person or first-person survival shooter with heavy ballistic recoil"
    },
    "Fortnite": {
        "franchise": "Fortnite",
        "genre": "Battle Royale / Building",
        "developer": "Epic Games",
        "aliases": ["Fortnite Battle Royale", "Fortnite BR"],
        "hud_elements": [
            "wood, stone, metal building material counters bottom-right",
            "five-slot inventory weapon bar bottom-right",
            "bright green health bar and blue shield bar bottom-left",
            "compass bar top center",
            "Battle Bus flight trajectory"
        ],
        "key_weapons": ["Pump Shotgun", "Assault Rifle", "Shockwave Grenade", "Heavy Sniper", "Slurp Juice"],
        "locations": ["Tilted Towers", "Pleasant Park", "Retail Row", "Battle Royale Island"],
        "visual_style": "Vibrant colorful stylized visuals, third-person perspective, pickaxe harvesting and building"
    },
    "Minecraft": {
        "franchise": "Minecraft",
        "genre": "Sandbox Survival / Adventure",
        "developer": "Mojang Studios",
        "aliases": ["Minecraft", "MC", "Minecraft Java", "Minecraft Bedrock", "Minecraft Survival"],
        "hud_elements": [
            "ten heart health meter bottom-left above hotbar",
            "ten drumstick hunger meter bottom-right above hotbar",
            "nine-slot inventory hotbar center bottom",
            "green experience level number center bottom above hotbar",
            "armor shield meter above hearts",
            "oxygen bubbles meter when submerged"
        ],
        "key_weapons": ["Netherite Sword", "Diamond Sword", "Bow", "Crossbow", "Trident", "Mace", "Netherite Axe"],
        "characters": ["Steve", "Alex", "Creeper", "Ender Dragon", "Warden", "Wither", "Villager", "Iron Golem"],
        "locations": ["Overworld", "Nether", "The End", "Deep Dark", "Ancient City", "Woodland Mansion", "Bastion"],
        "visual_style": "Iconic blocky voxel aesthetic with 16x16 pixel textures, first-person mining, crafting and building"
    },
    "Apex Legends": {
        "franchise": "Apex Legends",
        "genre": "Hero Battle Royale / First-Person Shooter",
        "developer": "Respawn Entertainment",
        "aliases": ["Apex", "Apex Legends"],
        "hud_elements": [
            "squad member health and shield bars bottom-left",
            "tactical and ultimate ability cooldown icons bottom-center",
            "compass bar top center",
            "squads remaining and kill counter top-right",
            "evo shield damage counter"
        ],
        "key_weapons": ["R-301", "Wingman", "Peacekeeper", "Flatline", "Volt", "Mastiff", "Kraber"],
        "characters": ["Wraith", "Octane", "Pathfinder", "Bloodhound", "Lifeline", "Horizon", "Bangalore"],
        "locations": ["Kings Canyon", "World's Edge", "Olympus", "Storm Point", "Broken Moon"],
        "visual_style": "High-mobility first-person hero movement shooter with sliding, zip-lines, and futuristic sci-fi aesthetic"
    },
    "Rocket League": {
        "franchise": "Rocket League",
        "genre": "Vehicular Soccer / Sports",
        "developer": "Psyonix",
        "aliases": ["Rocket League", "RL"],
        "hud_elements": [
            "boost gauge percentage 0-100 bottom-right",
            "match score and game clock top center",
            "ball cam toggle indicator bottom-left",
            "demolition and goal banner popups"
        ],
        "key_vehicles": ["Octane", "Fennec", "Dominus", "Batmobile", "Breakout"],
        "locations": ["DFH Stadium", "Mannfield", "Champions Field", "Utopia Coliseum", "Neo Tokyo"],
        "visual_style": "Acrobat rocket-powered cars hitting a giant soccer ball in enclosed neon stadium arenas"
    },
    "Forza Horizon 5": {
        "franchise": "Forza",
        "genre": "Sim-Cade Racing",
        "developer": "Playground Games / Xbox Game Studios",
        "aliases": ["FH5", "Forza Horizon 5", "Forza 5", "Forza Horizon"],
        "hud_elements": [
            "circular tachometer and digital gear/speedometer dial bottom-right",
            "race position (e.g. 1st / 12) top-left",
            "lap counter top-left",
            "skill score chain counter top center (Drift, Clean Racing, Near Miss, Burnout)",
            "Rewind prompt 'Press Y to Rewind'"
        ],
        "key_vehicles": ["Ferrari", "Lamborghini", "Porsche 911", "Ford Bronco", "Koenigsegg Jesko", "Corvette"],
        "locations": ["Mexico", "Guanajuato", "Gran Caldera Volcano", "Horizon Festival Stage", "Baja Circuit"],
        "visual_style": "Photorealistic open world racing, dynamic tire smoke, cockpit view or third-person chase camera"
    },
    "Cyberpunk 2077": {
        "franchise": "Cyberpunk",
        "genre": "Action RPG / Cyberpunk",
        "developer": "CD Projekt Red",
        "aliases": ["Cyberpunk", "CP2077", "Phantom Liberty"],
        "hud_elements": [
            "red or yellow cyberware HUD elements",
            "circular mini-map top-right",
            "RAM cyberdeck bar",
            "stamina meter center-left",
            "phone holo-call avatar top-left",
            "wanted star system"
        ],
        "key_weapons": ["Mantis Blades", "Monowire", "Gorilla Arms", "Malorian Arms 3516", "Smart Shotgun", "Katana"],
        "characters": ["V", "Johnny Silverhand", "Solomon Reed", "Judy Alvarez", "Panam Palmer"],
        "locations": ["Night City", "Dogtown", "Watson", "Pacifica", "Badlands", "Corpo Plaza"],
        "visual_style": "Dense first-person futuristic metropolis with neon holograms, ray-traced reflections, cybernetic combat"
    },
    "Elden Ring": {
        "franchise": "Elden Ring",
        "genre": "Action RPG / Soulslike",
        "developer": "FromSoftware",
        "aliases": ["Elden Ring", "Shadow of the Erdtree", "ER"],
        "hud_elements": [
            "red HP, blue FP, and green Stamina bars stacked top-left",
            "diamond d-pad equipment slot selector bottom-left",
            "boss health bar spanning bottom center of screen",
            "compass bar top center",
            "runes counter bottom-right"
        ],
        "key_weapons": ["Moonveil", "Rivers of Blood", "Bloodhound's Fang", "Dark Moon Greatsword", "Blasphemous Blade"],
        "characters": ["Tarnished", "Malenia", "Radahn", "Ranni", "Messmer the Impaler", "Torrent"],
        "locations": ["The Lands Between", "Limgrave", "Caelid", "Liurnia", "Leyndell", "Realm of Shadow"],
        "visual_style": "Dark fantasy open world, giant glowing Erdtree on horizon, third-person roll and strike combat"
    }
}


def lookup_game_by_alias(query: str) -> Optional[Dict[str, Any]]:
    """Checks query against registered game titles and known aliases."""
    if not query:
        return None
    cleaned = query.strip().lower()
    for official_name, spec in GAME_REGISTRY.items():
        if cleaned == official_name.lower():
            return {
                "name": official_name,
                "is_confirmed": True,
                "is_uncertain": False,
                **spec
            }
        for alias in spec.get("aliases", []):
            if cleaned == alias.lower():
                return {
                    "name": official_name,
                    "is_confirmed": True,
                    "is_uncertain": False,
                    **spec
                }
    return None


def match_game_from_evidence(evidence_texts: List[str]) -> Optional[Dict[str, Any]]:
    """
    Evaluates evidence points (detected text, weapons, HUD elements, characters)
    against the game registry to find the highest-confidence registered game match.
    """
    if not evidence_texts:
        return None

    combined_text = " ".join(evidence_texts).lower()
    best_match = None
    best_score = 0

    for official_name, spec in GAME_REGISTRY.items():
        score = 0
        # 1. Direct name/alias match with word boundaries
        if re.search(rf"\b{re.escape(official_name.lower())}\b", combined_text):
            score += 40
        for alias in spec.get("aliases", []):
            if re.search(rf"\b{re.escape(alias.lower())}\b", combined_text):
                score += 35
                break

        # 2. Key characters match
        for char in spec.get("characters", []):
            if re.search(rf"\b{re.escape(char.lower())}\b", combined_text):
                score += 25

        # 3. Key weapons match
        for weapon in spec.get("key_weapons", []):
            if re.search(rf"\b{re.escape(weapon.lower())}\b", combined_text):
                score += 20

        # 4. Key vehicles match
        for vehicle in spec.get("key_vehicles", []):
            if re.search(rf"\b{re.escape(vehicle.lower())}\b", combined_text):
                score += 20

        # 5. Key locations match
        for loc in spec.get("locations", []):
            if re.search(rf"\b{re.escape(loc.lower())}\b", combined_text):
                score += 20

        # 6. HUD elements match
        for hud in spec.get("hud_elements", []):
            hud_terms = [t.strip("(),'\"") for t in hud.lower().split() if len(t.strip("(),'\"")) >= 4]
            if any(re.search(rf"\b{re.escape(term)}\b", combined_text) for term in hud_terms):
                score += 10

        if score > best_score:
            best_score = score
            best_match = (official_name, spec, score)

    if best_match and best_score >= 30:
        name, spec, score = best_match
        confidence = min(0.98, round(score / 60.0, 2))
        return {
            "name": name,
            "franchise": spec["franchise"],
            "genre": spec["genre"],
            "confidence": confidence,
            "match_score": score,
            "is_confirmed": bool(confidence >= 0.70),
            "is_uncertain": bool(confidence < 0.70)
        }

    return None


def format_game_knowledge_prompt() -> str:
    """Returns concise catalog of known game franchises for inclusion in the AI prompt."""
    lines = []
    for name, spec in GAME_REGISTRY.items():
        weapons = ", ".join(spec.get("key_weapons", [])[:3])
        hud = ", ".join(spec.get("hud_elements", [])[:2])
        lines.append(f"- {name} ({spec['genre']}): HUD: {hud}; Weapons: {weapons}")
    return "\n".join(lines)
