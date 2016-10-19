
# DOT Time Tool

from os import path, curdir
from pymongo import MongoClient
from geopy import distance
import cherrypy
import os
import csv
import math
import copy
import random
import json
import argparse
import numpy

class StackMirror():

    def __init__(self, dbName, collectionName):
        self.db = MongoClient()[dbName]
        self.collection = self.db[collectionName]

    @cherrypy.expose
    def index(self):
        return file("index.html")


##################################################################################
#### Return filters in pymongodb format
##################################################################################
    def getFilters(self, json):
        filters = []

        startHour = json['startHour']
        endHour   = json['endHour']
        if(startHour != -1 and endHour != -1):
            filters.append({"hour": {"$gte":startHour,"$lte":startHour}})
        elif(startHour == -1 and endHour != -1):
            filters.append({"hour": {"$lte":startHour}})
        elif(startHour != -1 and endHour == -1):
            filters.append({"hour": {"$gte":startHour}})

        dayOfWeek = json['dayOfWeek']
        if(dayOfWeek != -1):
            filters.append({"dayOfWeek": dayOfWeek})

        month = json['month']
        if(month != -1):
            filters.append({"month": month})

        year = json['year']
        if(year != -1):
            filters.append({"year": year})

        direction = json['direction']
        if(direction != -1):
            filters.append({"DirectionRef": direction})

        lines = json['lines'].split(',')
        if(len(lines) > 0 and lines[0] != ''):
            filters.append({"PublishedLineName" : {'$in' : lines }})

        return filters

##################################################################################
#### Compute avg speed per line
##################################################################################
    def computeSpeedPerLine(self, records):

        buses = {}
        for r in records:
            b = r["DatedVehicleJourneyRef"]
            if b in buses:
                buses[b].append(r)
            else:
                buses[b] = []
                buses[b].append(r)

        for b in buses:
            buses[b].sort(key = lambda r : r['RecordedAtTime'])

        # Compute speed between successive pings
        speedsPerBus = {}
        lines = {}
        avgSpeedsPerBus = {}
        for b in buses:
            speedsPerBus[b] = []
            for i in range(1,len(buses[b])):
                p0 = [buses[b][i-1]['VehicleLocation'][1],buses[b][i-1]['VehicleLocation'][0]] #lat,lon format
                p1 = [buses[b][i]['VehicleLocation'][1],buses[b][i]['VehicleLocation'][0]]

                if buses[b][i-1]['PublishedLineName'] != buses[b][i]['PublishedLineName']:
                    print 'Different line names!!'

                dist = distance.distance(p0,p1).meters

                t0 = buses[b][i-1]['RecordedAtTime']
                t1 = buses[b][i]['RecordedAtTime']

                if (t1-t0).seconds > 0:
                    speedMs = (dist / (t1-t0).seconds) # in meters / seconds
                else:
                    speedMs = 0
                speedKh = speedMs * 3.6
                speedMh = speedKh * 0.621371192

                # print buses[b][i]['PublishedLineName'],p0,p1,dist,(t1-t0).seconds,speedKh

                speedsPerBus[b].append(speedMh)
                lines[b] = buses[b][i]['PublishedLineName']
                # print b, lines[b], buses[b][i]['DatedVehicleJourneyRef']

        speedsPerLine = {}
        speedsPerLine["all"] = []
        for b in lines:
            line = lines[b]

            if line in speedsPerLine:
                speedsPerLine[line].extend(speedsPerBus[b])
            else:
                speedsPerLine[line] = []
                speedsPerLine[line].extend(speedsPerBus[b])

            speedsPerLine["all"].extends(speedsPerBus[b])

        return speedsPerLine

##################################################################################
#### Return records
##################################################################################
    def getRecords(self, geoJson, filters, selectionMode):

        geoJson = geoJson.copy()
        filters = filters[:]

        # modify geoJson so that it suits pymongo
        geoJson.pop("type")
        geoJson.pop("properties")
        if selectionMode == "segment":
            geoJson["$geometry"] = geoJson.pop("geometry")
            geoJson.pop("filterSize")

            query = {"VehicleLocation" : {"$geoWithin": geoJson}}
            filters.insert(0,query)
            
            cursor = self.collection.find({'$and': filters})
            return cursor

        elif selectionMode == "node":
            geoJson["$centerSphere"] = [[geoJson["geometry"]["coordinates"][0],geoJson["geometry"]["coordinates"][1]], geoJson["filterSize"] / 6378100.0] #radius given in radians
            geoJson.pop("geometry")
            geoJson.pop("filterSize")

            query = {"VehicleLocation" : {"$geoWithin": geoJson}}
            filters.insert(0,query)
            
            cursor = self.collection.find({'$and': filters})

            print query

            return cursor


##################################################################################
#### Return median ping time
##################################################################################
    def getMedianPingTimeByBus(self, records):
        times = {}
        buses = {}
        for e in records:
            b = e['DatedVehicleJourneyRef']
            if b in times:
                times[b].append(numpy.datetime64(e['RecordedAtTime']))
                buses[b].append(e)
            else:
                times[b] = []
                times[b].append(numpy.datetime64(e['RecordedAtTime']))
                buses[b] = []
                buses[b].append(e)

        medianTime = {}
        minTime = {}
        for b in times:
            minTime[b] = numpy.min(times[b])

            for i in range(0,len(times[b])):
                times[b][i] = times[b][i] - minTime[b]

            medianTime[b] = {}
            medianTime[b]['median'] = numpy.median(times[b]) + minTime[b]
            medianTime[b]['PublishedLineName'] = buses[b][0]['PublishedLineName']

        return medianTime

        

##################################################################################
#### Server: return requested trip info
##################################################################################
    @cherrypy.expose
    @cherrypy.tools.json_in()
    def getTripsCSV(self):
        inputJson = cherrypy.request.json
        filters  = self.getFilters(inputJson)
        features = inputJson['path']['features']
        selectionMode = inputJson['selectionMode']

        if selectionMode == "segment":
            buses = {}
            firstPing = {}
            lastPing  = {}
            for f in features:
                cursor = self.getRecords(f, filters, selectionMode)
                records = list(cursor)
                
                for e in records:
                    b = e['DatedVehicleJourneyRef']
                    if b in buses:
                        buses[b].append(e)
                        if e['RecordedAtTime'] < firstPing[b]:
                            firstPing[b] = e['RecordedAtTime']
                        if e['RecordedAtTime'] > lastPing[b]:
                            lastPing[b] = e['RecordedAtTime']
                    else:
                        buses[b] = []
                        buses[b].append(e)
                        lastPing[b] = e['RecordedAtTime']
                        firstPing[b] = e['RecordedAtTime']

            formatted = 'BusID,PublishedLineName,DirectionRef,FirstPing,LastPing\n'
            formatted += ''.join("%s,%s,%d,%s,%s\n"%(b,buses[b][0]['PublishedLineName'],buses[b][0]['DirectionRef'],firstPing[b],lastPing[b]) for b in buses)

        elif selectionMode == "node":
            numFeatures = len(features)

            buses = {}

            # first feature
            cursor = self.getRecords(features[0], filters, selectionMode)
            records = list(cursor)

            for e in records:
                b = e['DatedVehicleJourneyRef']
                if b in buses:
                    buses[b].append(e)
                else:
                    buses[b] = []
                    buses[b].append(e)

            medianFirstFeature = self.getMedianPingTimeByBus(records)

            # last feature
            cursor = self.getRecords(features[numFeatures-1], filters, selectionMode)
            records = list(cursor)
            medianSecondFeature = self.getMedianPingTimeByBus(records)

            formatted = 'BusID,PublishedLineName,DirectionRef,FirstPing,LastPing\n'
            for b in buses:
                if (b in medianFirstFeature) and (b in medianSecondFeature):
                    formatted += "%s,%s,%d,%s,%s\n"%(b,buses[b][0]['PublishedLineName'],buses[b][0]['DirectionRef'],medianFirstFeature[b]['median'],medianSecondFeature[b]['median'])

        cherrypy.response.headers['Content-Type']        = 'text/csv'
        cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=export.csv'

        return formatted

##################################################################################
#### Format records to csv
##################################################################################
    def getFormattedLine(self, record):
        return ("%s,%f,%f,%f,%s,%s,%s,%s,%s,%s,%s,%s")%\
                (record["OriginRef"],record["Bearing"],record["VehicleLocation"][1],record["VehicleLocation"][0],\
                 record["VehicleRef"],record["DestinationName"],record["JourneyPatternRef"],record["RecordedAtTime"],\
                 record["LineRef"],record["PublishedLineName"],record["DatedVehicleJourneyRef"],record["DirectionRef"])

##################################################################################
#### Server: return requested pings
##################################################################################
    @cherrypy.expose
    @cherrypy.tools.json_in()
    def getPingsCSV(self):
        inputJson = cherrypy.request.json
        filters  = self.getFilters(inputJson)
        features = inputJson['path']['features']
        selectionMode = inputJson['selectionMode']

        formatted = 'OriginRef,Bearing,Latitude,Longitude,VehicleRef,DestinationName,JourneyPatternRef,RecordedAtTime,LineRef,PublishedLineName,DatedVehicleJourneyRef,DirectionRef\n'
        for f in features:
            cursor = self.getRecords(f, filters, selectionMode)
            records = list(cursor)
            print len(records)
            formatted += ''.join(self.getFormattedLine(records[n])+'\n' for n in xrange(len(records)))

        cherrypy.response.headers['Content-Type']        = 'text/csv'
        cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=export.csv'

        return formatted


##################################################################################
#### Server: return requested avg speed as csv
##################################################################################
    @cherrypy.expose
    @cherrypy.tools.json_in()
    def getSpeedCSV(self):
        inputJson = cherrypy.request.json
        filters  = self.getFilters(inputJson)
        features = inputJson['path']['features']
        selectionMode = inputJson['selectionMode']

        formatted = 'segment,line,count,mean,median,std,min,max,percentile25th,percentile75th\n'
        if selectionMode == "segment":
            count = 0
            for f in features:
                cursor = self.getRecords(f, filters, selectionMode)
                records = list(cursor)
                speedByLine = self.computeSpeedPerLine(records)
                # print "============"+str(count)+"============="
                for l in avgSpeedPerLine:
                    if avgSpeedPerLine[l] >= 1.0:
                        formatted += "%d,%s,%d,%f,%f,%f,%f,%f,%f,%f\n"%(count,l,len(speedByLine[l]),numpy.mean(speedByLine[l]),numpy.median(speedByLine[l]),numpy.std(speedByLine[l]),\
                            numpy.min(speedByLine[l]),numpy.max(speedByLine[l]),numpy.percentile(speedByLine[l],25),numpy.percentile(speedByLine[l],75))
                count+=1
        elif selectionMode == "node":
            
            for i in range(1, len(features)):

                # speed between i-1 and i
                cursor = self.getRecords(features[i-1], filters, selectionMode)
                records = list(cursor)
                medianFirstFeature = self.getMedianPingTimeByBus(records)

                cursor = self.getRecords(features[i], filters, selectionMode)
                records = list(cursor)
                medianSecondFeature = self.getMedianPingTimeByBus(records)

                # using center of both features to compute distance
                p0 = [features[i-1]["geometry"]["coordinates"][1],features[i-1]["geometry"]["coordinates"][0]] #lat,lon format
                p1 = [features[i]["geometry"]["coordinates"][1],features[i]["geometry"]["coordinates"][0]]

                speedByLine = {}
                speedByLine["all"] = []
                for b in medianFirstFeature:
                    if (b in medianSecondFeature):
                        dist = distance.distance(p0,p1).meters
                        timeDelta = abs((medianSecondFeature[b]['median'] - medianFirstFeature[b]['median']).item().total_seconds())

                        if timeDelta > 0:
                            speedMs = (dist / timeDelta) # in meters / seconds
                        else:
                            speedMs = 0
                        speedKh = speedMs * 3.6
                        speedMh = speedKh * 0.621371192

                        line = medianFirstFeature[b]['PublishedLineName']
                        if line in speedByLine:
                            speedByLine[line].append(speedMh)
                        else:
                            speedByLine[line] = []
                            speedByLine[line].append(speedMh)

                        speedByLine["all"].append(speedMh)

                for l in speedByLine:
                    formatted += "%d,%s,%d,%f,%f,%f,%f,%f,%f,%f\n"%(i-1,l,len(speedByLine[l]),numpy.mean(speedByLine[l]),numpy.median(speedByLine[l]),numpy.std(speedByLine[l]),\
                        numpy.min(speedByLine[l]),numpy.max(speedByLine[l]),numpy.percentile(speedByLine[l],25),numpy.percentile(speedByLine[l],75))

        cherrypy.response.headers['Content-Type']        = 'text/csv'
        cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=export.csv'

        return formatted

##################################################################################
#### Server: return requested avg speed
##################################################################################
    @cherrypy.expose
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def getSpeed(self):
        inputJson = cherrypy.request.json
        filters  = self.getFilters(inputJson)
        features = inputJson['path']['features']
        selectionMode = inputJson['selectionMode']

        outputJson = {}
        count = 0
        for f in features:
            cursor = self.getRecords(f, filters, selectionMode)
            records = list(cursor)
            speedByLine = self.computeSpeedPerLine(records)
            outputJson[count] = {}
            for l in speedByLine:
                if speedByLine[l] >= 1.0:
                    outputJson[count][l] = {}
                    outputJson[count][l]['count'] = len(speedByLine[l])
                    outputJson[count][l]['mean'] = numpy.mean(speedByLine[l])
                    outputJson[count][l]['median'] = numpy.median(speedByLine[l])
                    outputJson[count][l]['std'] = numpy.std(speedByLine[l])
                    outputJson[count][l]['min'] = numpy.min(speedByLine[l])
                    outputJson[count][l]['max'] = numpy.max(speedByLine[l])
                    outputJson[count][l]['percentile25th'] = numpy.percentile(speedByLine[l],25)
                    outputJson[count][l]['percentile75th'] = numpy.percentile(speedByLine[l],75)
            count+=1

        return outputJson

def startServer(dbName, collectionName):
    # Uncomment below for server functionality
    PATH = os.path.abspath(os.path.dirname(__file__))
    class Root(object): pass
    cherrypy.tree.mount(StackMirror(dbName, collectionName), '/', config={
            '/': {
                    'tools.staticdir.on': True,
                    'tools.staticdir.dir': PATH,
                    'tools.staticdir.index': 'index.html',
                },
        })

    # sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
    cherrypy.config.update({'server.socket_host': '0.0.0.0',
                            'engine.autoreload.on': True
                            })
    cherrypy.engine.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='CherryPy server.')
    parser.add_argument('-d', action="store", dest="dbName", help='Database name', default='dot')
    parser.add_argument('-c', action="store", dest="collectionName", help='Collection name', default='bus')

    args = parser.parse_args()
    startServer(args.dbName, args.collectionName)