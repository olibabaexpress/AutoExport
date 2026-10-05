"""
Audio File Indexer
Pre-builds an index of all audio files on your drives for quick lookup.
Supports .wav, .mp3, .aiff, .flac formats.
"""

import os
import json
import logging
from pathlib import Path
from paths import data_file
from datetime import datetime
import threading

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.aiff', '.flac', '.m4a', '.ogg'}
INDEX_FILE = data_file("audio_index.json")
DRIVES_TO_INDEX = ['D:', 'F:', 'C:']  

class AudioFileIndexer:
    def __init__(self, index_file=INDEX_FILE):
        self.index_file = index_file
        self.index = {}
        self.load_index()
    

    def load_index(self):
        """Load existing index from disk."""
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, 'r') as f:
                    self.index = json.load(f)
                logger.info(f"Loaded audio index with {len(self.index)} files")
            except Exception as e:
                logger.error(f"Error loading index: {e}")
                self.index = {}
        else:
            logger.info("No existing index found, will create new one")
    
    def save_index(self):
        """Save index to disk."""
        try:
            with open(self.index_file, 'w') as f:
                json.dump(self.index, f, indent=2)
            logger.info(f"Saved audio index with {len(self.index)} files")
        except Exception as e:
            logger.error(f"Error saving index: {e}")

    @staticmethod
    def normalize_drive(drive: str) -> str:
        drive = drive.rstrip("\\/")
        if len(drive) == 2 and drive[1] == ":":
            return drive + "\\"
        return drive

    
    def build_index(self, drives=None, verbose=True):
        """
        Build/rebuild the audio file index.
        
        Args:
            drives: List of drive letters to index (e.g., ['D:', 'E:', 'F:'])
            verbose: If True, print progress to console
        
        Returns:
            Dictionary mapping filenames to their full paths
        """
        if drives is None:
            drives = DRIVES_TO_INDEX

        drives = [self.normalize_drive(d) for d in drives]

        self.index = {}
        total_files = 0
        
        for drive in drives:
            if not os.path.exists(drive):
                logger.warning(f"Drive {drive} not found, skipping")
                continue
            
            logger.info(f"Indexing drive {drive}...")
            if verbose:
                print(f"\nIndexing {drive}...")
            
            for root, dirs, files in os.walk(drive, topdown=True):
                root_norm = os.path.normcase(os.path.normpath(root))

                skip_dirs_global = {
                '__pycache__',
                '$RECYCLE.BIN',
                'System Volume Information',
                'AppData',
                'Program Files',
                'Program Files (x86)',
                'Windows',
                'ProgramData'
                }

                dirs[:] = [d for d in dirs if d not in skip_dirs_global]
                if root_norm.startswith(os.path.normcase(r"C:\Users")) and "\\appdata" in root_norm:
                    dirs[:] = []
                    continue
                
                

                for filename in files:
                    ext = Path(filename).suffix.lower()
                    if ext not in AUDIO_EXTENSIONS:
                        continue

                    full_path = os.path.normpath(os.path.join(root, filename))
                    key = filename

                    if key not in self.index:
                        self.index[key] = full_path
                        total_files += 1

                    if verbose and total_files % 1000 == 0:
                        print(f"  Indexed {total_files} files...")

                  
                
                # Print progress for each directory
                if verbose:
                    print(f"  Scanning: {root[:60]}...", end='\r')
        
        if verbose:
            print(f"\n✓ Indexing complete: {total_files} files found")
        
        logger.info(f"Index complete: {total_files} audio files indexed")
        self.save_index()
        return self.index
    
    def find_file(self, filename):
        """
        Find a file in the index.
        
        Args:
            filename: Filename to search for (e.g., "kick.wav")
        
        Returns:
            Full path if found, None otherwise
        """
        key = filename.lower()
        return self.index.get(key)
    
    def find_files_by_pattern(self, pattern):
        """
        Find files matching a pattern.
        
        Args:
            pattern: Partial filename to match (case-insensitive)
        
        Returns:
            List of full paths matching the pattern
        """
        pattern_lower = pattern.lower()
        return [path for name, path in self.index.items() if pattern_lower in name]
    
    def get_file_directory(self, filename):
        """
        Get the directory containing a file.
        
        Args:
            filename: Filename to search for
        
        Returns:
            Directory path if found, None otherwise
        """
        path = self.find_file(filename)
        if path:
            return os.path.dirname(path)
        return None
    
    def get_index_stats(self):
        """Get statistics about the current index."""
        if not self.index:
            return {
                'total_files': 0,
                'indexed_at': None,
                'drives_indexed': []
            }
        
        drives = set()
        for path in self.index.values():
            drive = os.path.splitdrive(path)[0]
            drives.add(drive)
        
        return {
            'total_files': len(self.index),
            'indexed_at': datetime.fromtimestamp(os.path.getmtime(self.index_file)).isoformat() if os.path.exists(self.index_file) else None,
            'drives_indexed': sorted(list(drives))
        }


# Standalone script to build index
def main():
    """Build the audio file index."""
    import sys
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    indexer = AudioFileIndexer()
    
    print("=" * 70)
    print("AUDIO FILE INDEXER")
    print("=" * 70)
    
    stats = indexer.get_index_stats()
    print(f"\nCurrent index stats:")
    print(f"  Total files: {stats['total_files']}")
    print(f"  Drives indexed: {', '.join(stats['drives_indexed'])}")
    if stats['indexed_at']:
        print(f"  Last indexed: {stats['indexed_at']}")
    
    user_input = input("\nRebuild index? (y/n): ").lower()
    if user_input == 'y':
        print("\nStarting indexing process...")
        print("(This may take several minutes for large drives)\n")
        indexer.build_index(verbose=True)
        
        stats = indexer.get_index_stats()
        print(f"\n✓ Index updated!")
        print(f"  Total files: {stats['total_files']}")
        print(f"  Drives indexed: {', '.join(stats['drives_indexed'])}")
    else:
        print("Skipped rebuilding index")


if __name__ == "__main__":
    main()