
import sys
import os

# Add the current directory to sys.path
sys.path.append(os.getcwd())

try:
    from plugins.companion_core.bubble_splitter import bubble_parts
    
    test_text = "我觉得这事儿真的不靠谱，特别是你昨天说的那样。其实我也想帮忙，但是太忙了。"
    print(f"Testing text: {test_text}")
    
    parts = bubble_parts(test_text)
    print("Split results:")
    for i, p in enumerate(parts):
        print(f"{i+1}: {p}")
        
    print("\nVerification Successful: No re.error raised.")
    
except Exception as e:
    print(f"\nVerification Failed: {e}")
    import traceback
    traceback.print_exc()
