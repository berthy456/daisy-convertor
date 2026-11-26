#!/bin/sh
# The AppImage is stored in parts: each is under GitHub's 100 MB per-file limit.
cat daisy-convertor-0.0.0-dev-6e4685a-x86_64.AppImage.part* \
    > daisy-convertor-0.0.0-dev-6e4685a-x86_64.AppImage
chmod +x daisy-convertor-0.0.0-dev-6e4685a-x86_64.AppImage
echo "daisy-convertor-0.0.0-dev-6e4685a-x86_64.AppImage is ready."
