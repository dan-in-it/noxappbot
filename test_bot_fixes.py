#!/usr/bin/env python3
"""
Test script to verify the bot fixes for handling excessive stickers/characters
"""

class MockUser:
    def __init__(self):
        self.id = 12345
        self.display_name = "TestUser"

class MockGuild:
    def __init__(self):
        self.name = "TestGuild"

class ApplicationHandler:
    """Simplified version of ApplicationHandler for testing"""
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.answers = []
        self.current_question = 0
    
    def validate_and_truncate_answer(self, content):
        """Validate and truncate answer to prevent Discord limits"""
        # Maximum characters per answer (leaving room for embed formatting and truncation message)
        MAX_ANSWER_LENGTH = 800
        TRUNCATION_MESSAGE = "... [Answer truncated due to length - {} characters total]"
        
        # Remove excessive whitespace
        content = ' '.join(content.split())
        
        # Truncate if too long
        if len(content) > MAX_ANSWER_LENGTH:
            # Calculate how much space we need for the truncation message
            truncation_msg = TRUNCATION_MESSAGE.format(len(content))
            available_space = MAX_ANSWER_LENGTH - len(truncation_msg)
            
            # Ensure we have enough space for meaningful content
            if available_space < 50:
                available_space = 50
                truncation_msg = "... [Truncated]"
            
            truncated_content = content[:available_space].rstrip()
            
            # Try to truncate at a word boundary
            last_space = truncated_content.rfind(' ')
            if last_space > available_space * 0.8:  # Only if we don't lose too much
                truncated_content = truncated_content[:last_space]
            
            return f"{truncated_content}{truncation_msg}"
        
        return content
    
    def is_spam_answer(self, content):
        """Check if answer appears to be spam (repeated characters/stickers)"""
        if len(content) < 10:
            return False
        
        # Check for excessive repeated characters (more than 80% of the content)
        char_counts = {}
        for char in content:
            char_counts[char] = char_counts.get(char, 0) + 1
        
        # If any single character makes up more than 80% of the content, it's likely spam
        max_char_ratio = max(char_counts.values()) / len(content)
        if max_char_ratio > 0.8:
            return True
        
        # Check for repeated patterns (like sticker spam)
        # Look for sequences of the same 2-3 character pattern repeated
        for pattern_length in [2, 3, 4]:
            if len(content) >= pattern_length * 10:  # Only check if content is long enough
                pattern = content[:pattern_length]
                pattern_count = content.count(pattern)
                if pattern_count > len(content) / (pattern_length * 2):  # Pattern appears too frequently
                    return True
        
        return False

def test_spam_detection():
    """Test the spam detection functionality"""
    print("Testing spam detection...")
    
    # Create a mock application handler
    handler = ApplicationHandler(MockUser(), MockGuild())
    
    # Test cases
    test_cases = [
        # Normal text - should not be spam
        ("This is a normal answer to the question.", False),
        
        # Short repeated text - should not be spam
        ("Yes yes yes", False),
        
        # Excessive sticker spam - should be spam
        ("🎉" * 100, True),
        
        # Excessive character spam - should be spam
        ("." * 200, True),
        
        # Mixed but mostly spam - should be spam
        ("a" * 80 + "bcdefg", True),
        
        # Repeated pattern spam - should be spam
        ("ab" * 50, True),
        
        # Normal text with some repetition - should not be spam
        ("I really really want to join this guild because it seems amazing!", False),
    ]
    
    for text, expected_spam in test_cases:
        result = handler.is_spam_answer(text)
        status = "✅" if result == expected_spam else "❌"
        print(f"{status} Text length {len(text)}: {'SPAM' if result else 'OK'} (expected {'SPAM' if expected_spam else 'OK'})")
        if result != expected_spam:
            print(f"   Text preview: {text[:50]}...")

def test_truncation():
    """Test the answer truncation functionality"""
    print("\nTesting answer truncation...")
    
    # Create a mock application handler
    handler = ApplicationHandler(MockUser(), MockGuild())
    
    # Test cases
    test_cases = [
        # Short text - should not be truncated
        "This is a short answer.",
        
        # Long text - should be truncated
        "This is a very long answer that exceeds the maximum length limit. " * 20,
        
        # Text with excessive whitespace - should be cleaned
        "This    has    too    much    whitespace    between    words.",
        
        # Very long text with no spaces - should be truncated
        "a" * 1000,
    ]
    
    for text in test_cases:
        result = handler.validate_and_truncate_answer(text)
        truncated = "[Answer truncated" in result
        print(f"Original length: {len(text)}, Result length: {len(result)}, Truncated: {truncated}")
        if len(result) > 900:
            print(f"❌ Result still too long: {len(result)} characters")
        else:
            print(f"✅ Result within limits")

def test_edge_cases():
    """Test edge cases"""
    print("\nTesting edge cases...")
    
    handler = ApplicationHandler(MockUser(), MockGuild())
    
    # Empty string
    result = handler.validate_and_truncate_answer("")
    print(f"Empty string: '{result}' (length: {len(result)})")
    
    # Very short spam
    result = handler.is_spam_answer("aaa")
    print(f"Short spam detection: {result} (should be False)")
    
    # Unicode characters
    unicode_text = "🎉🎊🎈" * 50
    spam_result = handler.is_spam_answer(unicode_text)
    truncate_result = handler.validate_and_truncate_answer(unicode_text)
    print(f"Unicode spam: detected={spam_result}, truncated_length={len(truncate_result)}")

if __name__ == "__main__":
    print("Testing bot fixes for excessive stickers/characters\n")
    test_spam_detection()
    test_truncation()
    test_edge_cases()
    print("\nTest completed!")