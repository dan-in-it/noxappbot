"""
Time parsing utilities for the Discord bot
"""

def parse_time_string(time_str):
    """Parse time string like '10m', '1h', '30m', '2h' into seconds"""
    if not time_str:
        return None
    
    time_str = time_str.lower().strip()
    
    if time_str.endswith('m'):
        try:
            minutes = int(time_str[:-1])
            if minutes < 1 or minutes > 10080:  # Max 1 week in minutes
                return None
            return minutes * 60
        except ValueError:
            return None
    elif time_str.endswith('h'):
        try:
            hours = int(time_str[:-1])
            if hours < 1 or hours > 168:  # Max 1 week in hours
                return None
            return hours * 3600
        except ValueError:
            return None
    else:
        # Try to parse as just a number (assume hours for backwards compatibility)
        try:
            hours = int(time_str)
            if hours < 1 or hours > 168:
                return None
            return hours * 3600
        except ValueError:
            return None

def format_time_duration(seconds):
    """Format seconds into a human-readable duration"""
    if seconds < 3600:  # Less than an hour
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"