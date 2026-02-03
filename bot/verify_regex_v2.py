
import sys
import os

# Add the directory containing bubble_splitter.py to sys.path
# Path: bot/plugins/companion_core
current_dir = os.getcwd() # bot
target_dir = os.path.join(current_dir, 'plugins', 'companion_core')
sys.path.insert(0, target_dir)

try:
    import bubble_splitter
    print("Successfully imported bubble_splitter locally.")
    
    test_text = "我觉得这事儿真的不靠谱，特别是你昨天说的那样。其实我也想帮忙，但是太忙了。"
    print(f"Testing text: {test_text}")
    
    parts = bubble_splitter.bubble_parts(test_text)
    print("Split results:")
    for i, p in enumerate(parts):
        print(f"{i+1}: {p}")

    # Explicitly test the regex just in case
    import re
    line = "我觉得这事儿真的不靠谱"
    pattern_words = "|".join(bubble_splitter.SEMANTIC_BREAK_WORDS)
    break_pattern = f"([。！？!?]|{pattern_words})"
    print(f"\nRegex used: {break_pattern}")
    re.compile(break_pattern) # Try to compile it
    print("Regex compilation successful.")
        
    print("\nVerification Successful: No re.error raised.")
    
except Exception as e:
    print(f"\nVerification Failed: {e}")
    import traceback
    traceback.print_exc()
