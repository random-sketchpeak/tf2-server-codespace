#!/usr/bin/env python3
"""
TF2 Steam Inventory Analyzer
Analyzes Team Fortress 2 inventory and estimates item values across different markets.
"""

import requests
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import sys
from pathlib import Path

# Configuration
STEAM_API_BASE = "https://steamcommunity.com"
STEAM_MARKET_API = f"{STEAM_API_BASE}/market/priceoverview/"
TF2_APP_ID = 440
TF2_CONTEXT_ID = 2
OUTPUT_FILE = "tf2_inventory.json"
REQUEST_DELAY = 0.5  # Seconds between requests to respect rate limits


@dataclass
class ItemPrice:
    """Represents pricing data for an item"""
    steam: Optional[str] = None
    scrap_tf: Optional[str] = None
    marketplace_tf: Optional[str] = None


@dataclass
class InventoryItem:
    """Represents a TF2 inventory item"""
    name: str
    tradable: bool
    marketable: bool
    class_id: str
    instance_id: str
    quantity: int
    prices: Dict[str, Optional[str]]
    asset_id: str = ""


class TF2InventoryAnalyzer:
    """Main analyzer class for TF2 inventory"""

    def __init__(self, steam_id64: str):
        """
        Initialize the analyzer with a SteamID64.
        
        Args:
            steam_id64: 64-bit Steam ID
        """
        self.steam_id64 = steam_id64
        self.inventory_items: List[InventoryItem] = []
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'TF2-Inventory-Analyzer/1.0'
        })

    def fetch_inventory(self) -> bool:
        """
        Fetch TF2 inventory from Steam Community API.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"📦 Fetching inventory for SteamID64: {self.steam_id64}")
            
            url = (
                f"{STEAM_API_BASE}/inventory/{self.steam_id64}/"
                f"{TF2_APP_ID}/{TF2_CONTEXT_ID}/?l=english&count=5000"
            )
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data.get('success'):
                print("❌ Inventory is private or unavailable")
                return False
            
            if 'assets' not in data or not data['assets']:
                print("⚠️  Empty inventory or no TF2 items found")
                return False
            
            # Parse inventory
            self._parse_inventory(data)
            print(f"✅ Successfully fetched {len(self.inventory_items)} items")
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching inventory: {e}")
            return False
        except (ValueError, KeyError) as e:
            print(f"❌ Error parsing inventory data: {e}")
            return False

    def _parse_inventory(self, data: Dict) -> None:
        """
        Parse inventory JSON and extract items.
        
        Args:
            data: Raw inventory JSON from Steam API
        """
        assets = {item['assetid']: item for item in data.get('assets', [])}
        descriptions = {
            (item['classid'], item.get('instanceid', '0')): item
            for item in data.get('descriptions', [])
        }
        
        items_by_id = {}
        
        for asset in data.get('assets', []):
            asset_id = asset['assetid']
            class_id = asset['classid']
            instance_id = asset.get('instanceid', '0')
            
            desc_key = (class_id, instance_id)
            if desc_key not in descriptions:
                continue
            
            description = descriptions[desc_key]
            
            # Extract properties
            name = description.get('market_hash_name', description.get('name', 'Unknown'))
            tradable = self._check_tradable(description)
            marketable = self._check_marketable(description)
            
            # Group by market_hash_name for quantity calculation
            if name not in items_by_id:
                items_by_id[name] = {
                    'name': name,
                    'tradable': tradable,
                    'marketable': marketable,
                    'class_id': class_id,
                    'instance_id': instance_id,
                    'quantity': 0,
                    'asset_id': asset_id
                }
            
            items_by_id[name]['quantity'] += 1
        
        # Convert to InventoryItem objects
        for item_data in items_by_id.values():
            item = InventoryItem(
                name=item_data['name'],
                tradable=item_data['tradable'],
                marketable=item_data['marketable'],
                class_id=item_data['class_id'],
                instance_id=item_data['instance_id'],
                quantity=item_data['quantity'],
                asset_id=item_data['asset_id'],
                prices={}
            )
            self.inventory_items.append(item)

    @staticmethod
    def _check_tradable(description: Dict) -> bool:
        """Check if item is tradable"""
        for tag in description.get('tags', []):
            if tag.get('category') == 'tradable':
                return tag.get('name') != 'Not Tradable'
        return True

    @staticmethod
    def _check_marketable(description: Dict) -> bool:
        """Check if item is marketable"""
        for tag in description.get('tags', []):
            if tag.get('category') == 'marketable':
                return tag.get('name') != 'Not Marketable'
        return True

    def fetch_prices(self) -> None:
        """Fetch prices for all marketable items from Steam Market"""
        print("\n💰 Fetching Steam Market prices...")
        
        marketable_items = [item for item in self.inventory_items if item.marketable]
        
        for i, item in enumerate(marketable_items):
            try:
                price = self._fetch_steam_price(item.name)
                item.prices['steam'] = price
                
                # Rate limiting
                if i < len(marketable_items) - 1:
                    time.sleep(REQUEST_DELAY)
                    
            except Exception as e:
                print(f"⚠️  Failed to fetch price for {item.name}: {e}")
                item.prices['steam'] = None

    def _fetch_steam_price(self, market_hash_name: str) -> Optional[str]:
        """
        Fetch price from Steam Community Market.
        
        Args:
            market_hash_name: Market hash name of the item
            
        Returns:
            str: Price string or None if not found
        """
        try:
            params = {
                'country': 'US',
                'currency': 1,  # USD
                'appid': TF2_APP_ID,
                'market_hash_name': market_hash_name
            }
            
            response = self.session.get(STEAM_MARKET_API, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('success'):
                lowest_price = data.get('lowest_price')
                if lowest_price:
                    return lowest_price
                    
        except Exception as e:
            # Silently handle individual item failures
            pass
        
        return None

    def find_best_sell_item(self) -> Optional[InventoryItem]:
        """
        Find the best item to sell based on price and marketability.
        
        Returns:
            InventoryItem: Best item or None if no marketable items with prices
        """
        candidates = [
            item for item in self.inventory_items
            if item.marketable and item.prices.get('steam')
        ]
        
        if not candidates:
            return None
        
        # Sort by price (highest first)
        def price_to_float(price_str: str) -> float:
            if not price_str:
                return 0.0
            # Extract numeric value from price string (e.g., "$2.50" -> 2.50)
            cleaned = price_str.replace('$', '').replace(',', '')
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        
        best = max(candidates, key=lambda x: price_to_float(x.prices.get('steam', '$0')))
        return best

    def save_to_json(self) -> bool:
        """
        Save inventory items to JSON file.
        
        Returns:
            bool: True if successful
        """
        try:
            output_data = {
                'steam_id': self.steam_id64,
                'total_items': len(self.inventory_items),
                'fetch_timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'items': []
            }
            
            for item in self.inventory_items:
                item_dict = {
                    'name': item.name,
                    'tradable': item.tradable,
                    'marketable': item.marketable,
                    'quantity': item.quantity,
                    'prices': item.prices
                }
                output_data['items'].append(item_dict)
            
            with open(OUTPUT_FILE, 'w') as f:
                json.dump(output_data, f, indent=2)
            
            print(f"\n✅ Inventory saved to {OUTPUT_FILE}")
            return True
            
        except Exception as e:
            print(f"❌ Error saving to JSON: {e}")
            return False

    def print_summary(self) -> None:
        """Print a summary report of the inventory"""
        print("\n" + "="*60)
        print("📊 TF2 INVENTORY SUMMARY")
        print("="*60)
        
        print(f"\n👤 SteamID64: {self.steam_id64}")
        print(f"📦 Total Items: {len(self.inventory_items)}")
        print(f"🏷️  Tradable: {sum(1 for i in self.inventory_items if i.tradable)}")
        print(f"💳 Marketable: {sum(1 for i in self.inventory_items if i.marketable)}")
        
        # Calculate total value
        total_value = 0.0
        priced_items = 0
        
        for item in self.inventory_items:
            if item.prices.get('steam'):
                try:
                    price_str = item.prices['steam'].replace('$', '').replace(',', '')
                    total_value += float(price_str) * item.quantity
                    priced_items += 1
                except ValueError:
                    pass
        
        print(f"💰 Total Estimated Value: ${total_value:.2f} ({priced_items} priced items)")
        
        # Best sell recommendation
        best_item = self.find_best_sell_item()
        if best_item:
            print("\n🏆 BEST ITEM TO SELL:")
            print(f"   Name: {best_item.name}")
            print(f"   Price: {best_item.prices.get('steam', 'N/A')}")
            print(f"   Source: Steam Community Market")
            print(f"   Quantity: {best_item.quantity}")
        else:
            print("\n⚠️  No marketable items with prices available")
        
        # Top 10 items by value
        print("\n📈 TOP 10 ITEMS BY VALUE:")
        print("-" * 60)
        
        valued_items = []
        for item in self.inventory_items:
            if item.prices.get('steam'):
                try:
                    price_str = item.prices['steam'].replace('$', '').replace(',', '')
                    value = float(price_str) * item.quantity
                    valued_items.append((item, value))
                except ValueError:
                    pass
        
        valued_items.sort(key=lambda x: x[1], reverse=True)
        
        for idx, (item, value) in enumerate(valued_items[:10], 1):
            print(f"{idx:2d}. {item.name:<40} {item.prices['steam']:>8} x{item.quantity:<3} = ${value:>8.2f}")
        
        print("=" * 60 + "\n")

    def run(self) -> bool:
        """
        Run the complete analysis pipeline.
        
        Returns:
            bool: True if successful
        """
        if not self.fetch_inventory():
            return False
        
        self.fetch_prices()
        self.save_to_json()
        self.print_summary()
        
        return True


def main():
    """Main entry point"""
    print("🎮 TF2 Steam Inventory Analyzer")
    print("=" * 60)
    
    # Get SteamID64 from user
    if len(sys.argv) > 1:
        steam_id = sys.argv[1]
    else:
        steam_id = input("\n📝 Enter your SteamID64: ").strip()
    
    if not steam_id or not steam_id.isdigit():
        print("❌ Invalid SteamID64. Please enter a valid 64-bit Steam ID.")
        return
    
    # Run analyzer
    analyzer = TF2InventoryAnalyzer(steam_id)
    success = analyzer.run()
    
    if success:
        print("\n✅ Analysis complete! Results saved to tf2_inventory.json")
    else:
        print("\n❌ Analysis failed. Please check the error messages above.")


if __name__ == "__main__":
    main()
