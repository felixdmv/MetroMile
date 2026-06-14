import os
import json
import math
import sys
import time
import urllib.request

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def prune_loops(coords):
    if len(coords) < 4:
        return coords
        
    i = 0
    while i < len(coords) - 3:
        best_j = -1
        # Search from the end of the list for the largest loop starting at i
        for j in range(len(coords) - 1, i + 2, -1):
            p1 = coords[i]
            p2 = coords[j]
            d_straight = haversine(p1[0], p1[1], p2[0], p2[1]) * 1000.0 # meters
            
            if d_straight < 48.0: # If endpoints are within 48 meters (avenue width / narrow crossing)
                # Calculate path distance between i and j
                d_path = 0.0
                for k in range(i, j):
                    d_path += haversine(coords[k][0], coords[k][1], coords[k+1][0], coords[k+1][1]) * 1000.0
                
                # If the path walks a detour to cross
                if d_path > 80.0 and d_path > 2.0 * d_straight:
                    best_j = j
                    break # Found the largest loop starting at i
                    
        if best_j != -1:
            # Prune loop: connect coords[i] directly to coords[best_j]
            coords = coords[:i+1] + coords[best_j:]
            # Don't increment i; evaluate the new coords[i] against subsequent points
        else:
            i += 1
            
    return coords

def get_surface_coordinate(lat, lon):
    exclude_keywords = [
        'intercambiador', 'túnel', 'tunnel', 'estación', 'station', 
        'metro', 'subterráneo', 'subterraneo', 'escala', 'escalera', 
        'ascensor', 'lift', 'elevator', 'platform', 'andén', 'anden', 
        'via', 'vía', 'railway', 'platform', 'bypass', 'autovía', 
        'autovia', 'autopista', 'motorway', 'expressway', 'highway', 
        'link', 'périphérique', 'peripherique'
    ]
    
    url = f"https://router.project-osrm.org/nearest/v1/foot/{lon},{lat}?number=10"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MetroMileNearestTester/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
        waypoints = data.get('waypoints', [])
        for wp in waypoints:
            name = wp['name']
            name_lower = name.lower()
            
            exclude = False
            if not name:
                exclude = True
            else:
                for kw in exclude_keywords:
                    if kw in name_lower:
                        exclude = True
                        break
            if not exclude:
                loc = wp['location']
                return loc[1], loc[0]
                
        if waypoints:
            loc = waypoints[0]['location']
            return loc[1], loc[0]
    except Exception as e:
        pass
        
    return lat, lon

def get_single_leg_route(lon1, lat1, lon2, lat2):
    url = f"https://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson&steps=true"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MetroMileRouteTester/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
        if 'routes' in data and len(data['routes']) > 0:
            route = data['routes'][0]
            distance = route['distance']
            coords = []
            legs = route.get('legs', [])
            if legs:
                steps = legs[0].get('steps', [])
                for step in steps:
                    step_coords = step.get('geometry', {}).get('coordinates', [])
                    for pt in step_coords:
                        coord = [pt[1], pt[0], 800.0]
                        if not coords or coords[-1][:2] != coord[:2]:
                            coords.append(coord)
            return distance, coords
    except Exception as e:
        pass
    return None, None

def precalculate_all_routes():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    data_dir = os.path.join(project_root, "public", "data")
    
    cities_file = os.path.join(data_dir, "cities.json")
    if not os.path.exists(cities_file):
        print(f"No se encontró el archivo de ciudades: {cities_file}")
        return
        
    with open(cities_file, "r", encoding="utf-8") as f:
        cities = json.load(f)
        
    for city in cities:
        city_id = city["id"]
        city_dir = os.path.join(data_dir, "cities", city_id)
        metadata_file = os.path.join(city_dir, "metadata.json")
        
        if not os.path.exists(metadata_file):
            print(f"No hay metadatos para la ciudad: {city_id}")
            continue
            
        print(f"\nProcesando ciudad: {city['name']} ({city_id})...")
        
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)
            
        updated_metadata = []
        updated_routes_full = []
        
        for route_meta in metadata_list:
            route_id = route_meta["id"]
            route_file = os.path.join(city_dir, "routes", f"{route_id}.json")
            
            if not os.path.exists(route_file):
                print(f"  [Error] No se encontró el archivo de ruta: {route_file}")
                updated_metadata.append(route_meta)
                continue
                
            with open(route_file, "r", encoding="utf-8") as f:
                route = json.load(f)
                
            stops = route.get("stops", [])
            if len(stops) < 2:
                print(f"  Ruta {route['ref']} - {route['name']}: Insuficientes paradas ({len(stops)}). Se mantiene original.")
                updated_metadata.append(route_meta)
                updated_routes_full.append(route)
                continue
            
            # Check circularity
            name_lower = route["name"].lower()
            ref_upper = route["ref"].upper()
            desc_lower = route.get("description", "").lower()
            
            is_circular = (
                "circular" in name_lower or 
                "circular" in desc_lower or 
                "anden" in name_lower or
                "andén" in name_lower or
                ref_upper in ["L6", "L12", "L12A", "L12B"]
            )
            
            routing_stops = list(stops)
            start_stop = stops[0]
            end_stop = stops[-1]
            dist_ends = haversine(start_stop["lat"], start_stop["lon"], end_stop["lat"], end_stop["lon"])
            
            if is_circular and dist_ends > 0.05:
                routing_stops.append(start_stop)
                
            new_coords = []
            total_dist_meters = 0.0
            
            # Process leg by leg
            for i in range(len(routing_stops) - 1):
                s1 = routing_stops[i]
                s2 = routing_stops[i+1]
                
                d_straight = haversine(s1['lat'], s1['lon'], s2['lat'], s2['lon']) * 1000.0 # meters
                
                chosen_coords = None
                chosen_dist = None
                method_used = ""
                
                # 1. Option A: Unsnapped A -> B
                dist_unsnap, coords_unsnap = get_single_leg_route(s1['lon'], s1['lat'], s2['lon'], s2['lat'])
                time.sleep(0.02)
                
                if dist_unsnap is not None:
                    # Apply loop pruning to Option A
                    coords_unsnap_pruned = prune_loops(list(coords_unsnap))
                    dist_unsnap_pruned = sum(haversine(coords_unsnap_pruned[k][0], coords_unsnap_pruned[k][1], coords_unsnap_pruned[k+1][0], coords_unsnap_pruned[k+1][1]) for k in range(len(coords_unsnap_pruned)-1)) * 1000.0
                    
                    is_unsnap_detour = (dist_unsnap_pruned > 1.8 * d_straight + 400.0) or (dist_unsnap_pruned > 2.5 * d_straight)
                    if not is_unsnap_detour:
                        chosen_coords = coords_unsnap_pruned
                        chosen_dist = dist_unsnap_pruned
                        method_used = "unsnapped"
                        
                # 2. Option B: Snapped A -> B (using surface coordinates)
                if chosen_coords is None:
                    lat1_s, lon1_s = get_surface_coordinate(s1['lat'], s1['lon'])
                    lat2_s, lon2_s = get_surface_coordinate(s2['lat'], s2['lon'])
                    time.sleep(0.02)
                    
                    dist_snap, coords_snap = get_single_leg_route(lon1_s, lat1_s, lon2_s, lat2_s)
                    time.sleep(0.02)
                    
                    if dist_snap is not None:
                        coords_snap_pruned = prune_loops(list(coords_snap))
                        dist_snap_pruned = sum(haversine(coords_snap_pruned[k][0], coords_snap_pruned[k][1], coords_snap_pruned[k+1][0], coords_snap_pruned[k+1][1]) for k in range(len(coords_snap_pruned)-1)) * 1000.0
                        
                        is_snap_detour = (dist_snap_pruned > 1.8 * d_straight + 400.0) or (dist_snap_pruned > 2.5 * d_straight)
                        if not is_snap_detour:
                            chosen_coords = coords_snap_pruned
                            chosen_dist = dist_snap_pruned
                            method_used = "snapped"
                            
                # 3. Option C: Reversed Snapped B -> A
                if chosen_coords is None and 'lat1_s' in locals():
                    dist_opp_snap, coords_opp_snap = get_single_leg_route(lon2_s, lat2_s, lon1_s, lat1_s)
                    time.sleep(0.02)
                    if dist_opp_snap is not None:
                        coords_opp_snap_pruned = prune_loops(list(coords_opp_snap))
                        dist_opp_snap_pruned = sum(haversine(coords_opp_snap_pruned[k][0], coords_opp_snap_pruned[k][1], coords_opp_snap_pruned[k+1][0], coords_opp_snap_pruned[k+1][1]) for k in range(len(coords_opp_snap_pruned)-1)) * 1000.0
                        
                        is_opp_snap_detour = (dist_opp_snap_pruned > 1.8 * d_straight + 400.0) or (dist_opp_snap_pruned > 2.5 * d_straight)
                        if not is_opp_snap_detour:
                            chosen_coords = list(coords_opp_snap_pruned)
                            chosen_coords.reverse()
                            chosen_dist = dist_opp_snap_pruned
                            method_used = "opposite snapped"
                            
                # 4. Option D: Reversed Unsnapped B -> A
                if chosen_coords is None:
                    dist_opp_unsnap, coords_opp_unsnap = get_single_leg_route(s2['lon'], s2['lat'], s1['lon'], s1['lat'])
                    time.sleep(0.02)
                    if dist_opp_unsnap is not None:
                        coords_opp_unsnap_pruned = prune_loops(list(coords_opp_unsnap))
                        dist_opp_unsnap_pruned = sum(haversine(coords_opp_unsnap_pruned[k][0], coords_opp_unsnap_pruned[k][1], coords_opp_unsnap_pruned[k+1][0], coords_opp_unsnap_pruned[k+1][1]) for k in range(len(coords_opp_unsnap_pruned)-1)) * 1000.0
                        
                        is_opp_unsnap_detour = (dist_opp_unsnap_pruned > 1.8 * d_straight + 400.0) or (dist_opp_unsnap_pruned > 2.5 * d_straight)
                        if not is_opp_unsnap_detour:
                            chosen_coords = list(coords_opp_unsnap_pruned)
                            chosen_coords.reverse()
                            chosen_dist = dist_opp_unsnap_pruned
                            method_used = "opposite unsnapped"
                            
                # 5. Option E: Fallback to the shortest of all returned OSRM candidates to avoid straight line
                if chosen_coords is None:
                    candidates = []
                    if dist_unsnap is not None:
                        coords_unsnap_pruned = prune_loops(list(coords_unsnap))
                        dist_unsnap_pruned = sum(haversine(coords_unsnap_pruned[k][0], coords_unsnap_pruned[k][1], coords_unsnap_pruned[k+1][0], coords_unsnap_pruned[k+1][1]) for k in range(len(coords_unsnap_pruned)-1)) * 1000.0
                        candidates.append((dist_unsnap_pruned, coords_unsnap_pruned, "unsnapped detour"))
                    if 'dist_snap' in locals() and dist_snap is not None:
                        coords_snap_pruned = prune_loops(list(coords_snap))
                        dist_snap_pruned = sum(haversine(coords_snap_pruned[k][0], coords_snap_pruned[k][1], coords_snap_pruned[k+1][0], coords_snap_pruned[k+1][1]) for k in range(len(coords_snap_pruned)-1)) * 1000.0
                        candidates.append((dist_snap_pruned, coords_snap_pruned, "snapped detour"))
                    if 'dist_opp_snap' in locals() and dist_opp_snap is not None:
                        coords_opp_snap_pruned = prune_loops(list(coords_opp_snap))
                        dist_opp_snap_pruned = sum(haversine(coords_opp_snap_pruned[k][0], coords_opp_snap_pruned[k][1], coords_opp_snap_pruned[k+1][0], coords_opp_snap_pruned[k+1][1]) for k in range(len(coords_opp_snap_pruned)-1)) * 1000.0
                        rev = list(coords_opp_snap_pruned)
                        rev.reverse()
                        candidates.append((dist_opp_snap_pruned, rev, "opposite snapped detour"))
                    if 'dist_opp_unsnap' in locals() and dist_opp_unsnap is not None:
                        coords_opp_unsnap_pruned = prune_loops(list(coords_opp_unsnap))
                        dist_opp_unsnap_pruned = sum(haversine(coords_opp_unsnap_pruned[k][0], coords_opp_unsnap_pruned[k][1], coords_opp_unsnap_pruned[k+1][0], coords_opp_unsnap_pruned[k+1][1]) for k in range(len(coords_opp_unsnap_pruned)-1)) * 1000.0
                        rev = list(coords_opp_unsnap_pruned)
                        rev.reverse()
                        candidates.append((dist_opp_unsnap_pruned, rev, "opposite unsnapped detour"))
                        
                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        chosen_dist, chosen_coords, method_used = candidates[0]
                        print(f"    [Detour Forced] {route['ref']} {s1['name']} -> {s2['name']}: {method_used} ({chosen_dist:.1f}m vs Straight={d_straight:.1f}m)")
                    else:
                        # Fallback to straight line only if OSRM is totally down / returned nothing
                        chosen_coords = [
                            [s1['lat'], s1['lon'], 800.0],
                            [s2['lat'], s2['lon'], 800.0]
                        ]
                        chosen_dist = d_straight
                        method_used = "straight fallback"
                        print(f"    [Straight Fallback] OSRM failed completely for {route['ref']} {s1['name']} -> {s2['name']}")
                
                # Append coordinates, avoiding boundary duplicates
                for pt in chosen_coords:
                    if not new_coords or new_coords[-1][:2] != pt[:2]:
                        new_coords.append(pt)
                total_dist_meters += chosen_dist
                
            total_dist_km = total_dist_meters / 1000.0
            
            # Ensure distance is not zero
            if total_dist_km == 0.0:
                total_dist_km = route.get("distanceKm", 1.0)
                
            # Recalculate walking and running durations
            walk_time = int((total_dist_km / 4.5) * 3600)
            run_time = int((total_dist_km / 11.0) * 3600)
            
            # Update route object
            route["coords"] = new_coords
            route["distanceKm"] = round(total_dist_km, 2)
            route["estWalkingSeconds"] = walk_time
            route["estRunningSeconds"] = run_time
            
            # Save individual route JSON
            with open(route_file, "w", encoding="utf-8") as f:
                json.dump(route, f, ensure_ascii=False, indent=2)
                
            # Update metadata
            route_meta["distanceKm"] = route["distanceKm"]
            route_meta["estWalkingSeconds"] = walk_time
            route_meta["estRunningSeconds"] = run_time
            
            updated_metadata.append(route_meta)
            updated_routes_full.append(route)
            
            print(f"  [OK] {route['ref']} - {route['name']}: {len(stops)} paradas -> Distancia final: {route['distanceKm']} km")
            
        # Save updated metadata.json
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(updated_metadata, f, ensure_ascii=False, indent=2)
            
        # Save legacy single-file city JSON
        compat_file = os.path.join(data_dir, f"{city_id}.json")
        with open(compat_file, "w", encoding="utf-8") as f:
            json.dump(updated_routes_full, f, ensure_ascii=False, indent=2)
            
    print("\n¡Precalculo de superficie y correcciones de calles completado con éxito!")

if __name__ == "__main__":
    precalculate_all_routes()
