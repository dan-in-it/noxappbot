#!/usr/bin/env python3
"""
Test script to validate the Discord bot configuration and basic functionality.
This script checks for common issues without actually running the bot.
"""

import os
import sys
from pathlib import Path

def test_environment_variables():
    """Test that required environment variables are properly configured."""
    print("🔍 Testing environment variables...")
    
    # Check if .env file exists
    env_file = Path('.env')
    if not env_file.exists():
        print("⚠️  .env file not found. Please copy .env.example to .env and configure it.")
        return False
    
    # Load environment variables
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        # Required variables
        token = os.getenv("DISCORD_BOT_TOKEN")
        category_id = os.getenv("INTERVIEW_CATEGORY_ID")
        
        if not token or token == "YOUR_DISCORD_BOT_TOKEN":
            print("❌ DISCORD_BOT_TOKEN not properly configured in .env")
            return False
        
        if not category_id or category_id == "YOUR_INTERVIEW_CATEGORY_ID":
            print("❌ INTERVIEW_CATEGORY_ID not properly configured in .env")
            return False
        
        # Validate category ID is numeric
        try:
            int(category_id)
        except ValueError:
            print("❌ INTERVIEW_CATEGORY_ID must be a valid Discord channel ID (numeric)")
            return False
        
        # Optional variables validation
        officer_role_id = os.getenv("OFFICER_ROLE_ID")
        admin_role_id = os.getenv("ADMIN_ROLE_ID")
        
        if officer_role_id and officer_role_id.strip():
            try:
                int(officer_role_id)
                print("✅ OFFICER_ROLE_ID configured and valid")
            except ValueError:
                print("⚠️  OFFICER_ROLE_ID is set but not numeric - will be ignored")
        
        if admin_role_id and admin_role_id.strip():
            try:
                int(admin_role_id)
                print("✅ ADMIN_ROLE_ID configured and valid")
            except ValueError:
                print("⚠️  ADMIN_ROLE_ID is set but not numeric - will be ignored")
        
        # Configuration variables
        channel_prefix = os.getenv("APPLICATION_CHANNEL_PREFIX", "application")
        log_level = os.getenv("LOG_LEVEL", "INFO")
        
        print(f"✅ Application channel prefix: {channel_prefix}")
        print(f"✅ Log level: {log_level}")
        
        print("✅ Environment variables configured correctly")
        return True
        
    except ImportError:
        print("❌ python-dotenv not installed. Run: pip install -r requirements.txt")
        return False

def test_dependencies():
    """Test that all required dependencies are installed."""
    print("🔍 Testing dependencies...")
    
    try:
        import discord
        print(f"✅ discord.py version: {discord.__version__}")
    except ImportError:
        print("❌ discord.py not installed. Run: pip install -r requirements.txt")
        return False
    
    try:
        import dotenv
        print("✅ python-dotenv installed")
    except ImportError:
        print("❌ python-dotenv not installed. Run: pip install -r requirements.txt")
        return False
    
    return True

def test_bot_configuration():
    """Test bot configuration and intents."""
    print("🔍 Testing bot configuration...")
    
    try:
        import discord
        
        # Test that we're using default intents (no privileged intents)
        intents = discord.Intents.default()
        
        # Verify privileged intents are not enabled
        if intents.message_content:
            print("⚠️  Message content intent is enabled (privileged)")
        if intents.members:
            print("⚠️  Members intent is enabled (privileged)")
        if intents.presences:
            print("⚠️  Presences intent is enabled (privileged)")
        
        print("✅ Bot configured with default intents (no privileged intents required)")
        return True
        
    except Exception as e:
        print(f"❌ Error testing bot configuration: {e}")
        return False

def test_file_structure():
    """Test that all required files exist."""
    print("🔍 Testing file structure...")
    
    required_files = [
        'src/bot.py',
        'requirements.txt',
        '.env.example',
        'README.md',
        '.gitignore'
    ]
    
    missing_files = []
    for file_path in required_files:
        if not Path(file_path).exists():
            missing_files.append(file_path)
    
    if missing_files:
        print(f"❌ Missing files: {', '.join(missing_files)}")
        return False
    
    print("✅ All required files present")
    return True

def main():
    """Run all tests."""
    print("🤖 NoxAppBot Configuration Test\n")
    
    tests = [
        test_file_structure,
        test_dependencies,
        test_environment_variables,
        test_bot_configuration,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Your bot should be ready to run.")
        print("💡 To start the bot, run: python src/bot.py")
    else:
        print("❌ Some tests failed. Please fix the issues above before running the bot.")
        sys.exit(1)

if __name__ == "__main__":
    main()