from waybacktools import waybackmachine

wayback = waybackmachine()
print(wayback.download('https://github.com/'))