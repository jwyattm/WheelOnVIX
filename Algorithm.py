#region imports
from AlgorithmImports import *
#endregion

from datetime import timedelta


class OptionChainProviderPutProtection(QCAlgorithm):

    def Initialize(self):
        # set start/end date for backtest
        self.SetStartDate(2008, 1, 1)
        # set starting balance for backtest
        self.SetEndDate(2009, 1, 1)
        self.SetCash(100000)
        # add the underlying asset
        self.equity = self.AddEquity("SPY", Resolution.Minute)
        self.equity.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.spy = self.equity.Symbol
        self.exchange = self.Securities[self.spy].Exchange
        # add VIX data
        self.vix = self.AddData(CBOE, "VIX").Symbol
        # initialize IV indicator
        self.rank = 0
        # initialize the option contracts with empty strings
        self.putContract = str()
        self.callContract = str()
        self.contractsAdded = set()
        #initialize market open
        self.marketOpen = self.Time
        self.SPYstartprice = 0
        self.startPriceTrack = 0
        
        self.initialOptionPrice = 0 #price of option when sold / bought
        self.putContractQuantity = 0 #number of puts sold in high IV envir
        self.putStrike = 0 #strike price at which puts were sold
        self.getBack = False #are we trying to get assigned on calls?
        
        # parameters ------------------------------------------------------------
        self.DTE = 45 # target days till expiration
        self.VIXtarget = 25 # enter position at this lvl of IV indicator
        self.profitPercentTarget = 0.5 # % of max credit to close position on
        # ------------------------------------------------------------------------
    
        # schedule Plotting function 30 minutes after every market open
        self.Schedule.On(self.DateRules.EveryDay(self.spy), \
                        self.TimeRules.AfterMarketOpen(self.spy, 30), \
                        self.Plotting)
                        
        self.SetWarmup(timedelta(1))

    def OnData(self, data):
        
        if self.IsWarmingUp:
            return
        
        if self.startPriceTrack == 0:
            self.SPYstartprice = self.Securities[self.spy].Close 
            self.startPriceTrack = 1
    
        if self.exchange.ExchangeOpen:
            
            #if is in get getBack mode, sell calls
            if self.getBack == True and self.Portfolio[self.spy].Invested:
                self.SellGetBack(data)
            
            # buy underlying asset
            if not self.Portfolio[self.spy].Invested and self.Securities[self.vix].Price < self.VIXtarget and not self.getBack == True and not self.putContract and not self.callContract:
                self.SetHoldings(self.spy, 1)
                self.Log(str(self.Time) + " Buying SPY shares")
            
            # sell puts if VIX over VIX target
            if self.Securities[self.vix].Price > self.VIXtarget and not self.getBack == True:
                self.Liquidate(self.spy)
                self.SellPuts(data)
            
            # close put if reaches profit goal 
            if self.putContract:
                optionHistory = self.History(self.putContract, 1, Resolution.Minute) #get option price history
                if not optionHistory.empty and 'high' in optionHistory.columns:
                    currentOptionPrice = max(optionHistory["high"])
                    if currentOptionPrice <= self.initialOptionPrice * self.profitPercentTarget:
                        self.Liquidate(self.putContract)
                        self.Log(str(self.Time) + " Closed at % of max credit:" + str((self.initialOptionPrice - currentOptionPrice) / self.initialOptionPrice))
                        self.putContract = str()

    def SellPuts(self, data):
        
        #get option data
        if self.putContract == str():
            self.putContract = self.PutOptionsFilter(data)
            return
        
        # if not invested and option data added successfully, sell puts
        elif not self.Portfolio[self.putContract].Invested and data.ContainsKey(self.putContract):
            optionHistory = self.History(self.putContract, 1, Resolution.Minute) #get option price history if option
            if not optionHistory.empty and 'low' in optionHistory.columns:
                self.initialOptionPrice = min(optionHistory["low"])
                self.putContractQuantity = math.floor(((self.Portfolio.Cash / 100) / self.putContract.ID.StrikePrice))
                self.putStrike = self.putContract.ID.StrikePrice
                self.Sell(self.putContract, self.putContractQuantity)
                self.Log(str(self.Time) + " Selling puts")

    def PutOptionsFilter(self, data):

        contracts = self.OptionChainProvider.GetOptionContractList(self.spy, data.Time)
        self.underlyingPrice = self.Securities[self.spy].Price
        # filter the otm put options from the contract list which expire close to self.DTE num of days from now
        otm_puts = [i for i in contracts if i.ID.OptionRight == OptionRight.Put and
                                            i.ID.StrikePrice < self.underlyingPrice and
                                            self.DTE - 8 < (i.ID.Date - data.Time).days < self.DTE + 8]
        if len(otm_puts) > 0:
            # sort options by closest to self.DTE days from now and desired strike, and pick first
            putContract = sorted(sorted(otm_puts, key = lambda x: abs((x.ID.Date - self.Time).days - self.DTE)),
                                                     key = lambda x: abs(self.underlyingPrice - x.ID.StrikePrice))[0]
            if putContract not in self.contractsAdded:
                self.contractsAdded.add(putContract)
                # use AddOptionContract() to subscribe the data for specified contract
                self.AddOptionContract(putContract, Resolution.Minute)
            return putContract
        else:
            return str()
            
    def OnAssignmentOrderEvent(self, assignmentEvent):
        
        #if assigned on puts, sell calls 
        if self.putContract:
            self.Log("Assigned on puts; entering getBack")
            self.getBack = True
            self.putContract = str()
        
        #if assigned on calls, resume algo    
        elif self.callContract:
            self.Log("Assigned on calls; exiting getBack")
            self.getBack = False
            self.callContract = str()
            
            
    def SellGetBack(self, data):
        #get options data if contract empty or expired
        if self.callContract == str() or (self.callContract.ID.Date - self.Time).days <= -2:
            self.callContract = self.CallOptionsFilter(data)
            return
        
        #if invested and option data added succesfully, sell get back calls
        elif not self.Portfolio[self.callContract].Invested and data.ContainsKey(self.callContract) and self.Portfolio[self.spy].Invested:
            self.Sell(self.callContract, self.putContractQuantity)
            self.Log(str(self.Time) + "Selling calls")
            
            
    def CallOptionsFilter(self, data):
        
        contracts = self.OptionChainProvider.GetOptionContractList(self.spy, data.Time)
        self.underlyingPrice = self.Securities[self.spy].Price
        otm_calls = [i for i in contracts if i.ID.OptionRight == OptionRight.Call and 
                                            i.ID.StrikePrice > self.underlyingPrice and
                                            self.DTE - 10 < (i.ID.Date - data.Time).days < self.DTE + 10]
        if len(otm_calls) > 0:
            
            callContract = sorted(sorted(otm_calls, key = lambda x: abs((x.ID.Date - self.Time).days - self.DTE)),
                                                    key = lambda x: abs(x.ID.StrikePrice - self.putStrike))[0]
        
            if callContract not in self.contractsAdded:
                self.contractsAdded.add(callContract)
                
                self.AddOptionContract(callContract, Resolution.Minute)
            return callContract
        
        else:
            return str()
            

    def Plotting(self):
        # plot IV indicator
        self.Plot("Vol Chart", "Rank", self.VIXtarget)
        # plot indicator entry level
        self.Plot("Vol Chart", "VIX Price", self.Securities[self.vix].Price)
        # plot underlying's price
        self.Plot("Data Chart", self.spy, self.Securities[self.spy].Close)
        # plot strike of put option
        self.Plot("SPY Returns to Date", (self.Securities[self.spy].Close / self.SPYstartprice))
        
        option_invested = [x.Key for x in self.Portfolio if x.Value.Invested and x.Value.Type==SecurityType.Option]
        if option_invested:
                self.Plot("Data Chart", "strike", option_invested[0].ID.StrikePrice)
