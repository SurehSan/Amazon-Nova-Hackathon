# Inspiration
We wanted to build an app that would get rid of the trouble of excessively looking through the web for deals on hardware. We knew that people who had beginner knowledge to computer parts would search the web and often be met with lies, scams, and general confusion. This is why CacheHunt was made - to provide an ease of mind when search for computer deals.
<img width="1112" height="745" alt="image" src="https://github.com/user-attachments/assets/25fdbccd-f54c-43df-8669-bec048a9f1db" />


# What it does
Searches with an eBay API for hardware with filters, then uses Nova for the brain and chats with you about the best options based on what it found.
<img width="1080" height="967" alt="image" src="https://github.com/user-attachments/assets/5110011a-e442-468a-be1d-ea426c416b74" />


# How we built it
We started by setting up AWS and requesting Nova Pro access through Bedrock, while simultaneously getting our eBay developer credentials and enabling the APIs we needed. From there, we built a Lambda function that chains together eBay's Finding and Browse APIs to pull listing details and real sold-price history, then feeds that data alongside listing images into Nova Pro to generate a deal verdict. We wired it all up through API Gateway so our Manifest V3 Chrome extension could grab the item ID off any eBay listing page and display the full analysis in a clean popup.

# Challenges we ran into
A lot of our early time went toward just getting everything configured correctly. We had to figure out which APIs to enable, waiting on Nova Pro model access approval through Bedrock, and navigating AWS IAM permissions as relative beginners. Once we were building, we ran into an issue with our API permission issue that took a while to fix, and Nova's responses weren't always clean or using the API like we wanted, so we had to fix the logic and formatting quite a bit among other issues.

# Built With
amazon-api-gateway-apis:-ebay-finding-api
amazon-nova-pro-(via-amazon-bedrock)
amazon-web-services
aws-lambda
base64
chrome-extensions-manifest-v3-other:-static-msrp-reference-dataset-(custom-built-json)
ebay
ebay-browse-api-tools-&-libraries:-boto3-(aws-sdk-for-python)
encoding
html
html/css-cloud-/-ai:-amazon-web-services-(aws)
javascript
multimodal
nova
python

<img width="1184" height="896" alt="image" src="https://github.com/user-attachments/assets/20d828a7-f3bb-4cab-9755-b4dc9c9d4b74" />

