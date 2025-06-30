import {
  ArrowLeft,
  CheckCircle,
  ExternalLink,
  Key,
  Shield,
  Users,
  Zap,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function Component() {
  const requestedScopes = [
    "offline_access",
    "https://graph.microsoft.com/User.Read",
  ]

  const grantedScopes = [
    "openid",
    "profile",
    "email",
    "https://graph.microsoft.com/Application.Read.All",
    "https://graph.microsoft.com/Channel.Create",
    "https://graph.microsoft.com/Channel.Delete.All",
    "https://graph.microsoft.com/Channel.ReadBasic.All",
    "https://graph.microsoft.com/ChannelMessage.Read.All",
    "https://graph.microsoft.com/ChannelMessage.Send",
    "https://graph.microsoft.com/ChatMessage.Read",
    "https://graph.microsoft.com/ChatMessage.Send",
    "https://graph.microsoft.com/Team.ReadBasic.All",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/User.ReadBasic.All",
    "https://graph.microsoft.com/User.RevokeSessions.All",
  ]

  const features = [
    { name: "OAuth 2.0", icon: Shield },
    { name: "Azure AD Integration", icon: Key },
    { name: "Microsoft Graph API", icon: Zap },
    { name: "Single Sign-On", icon: Users },
  ]

  const setupSteps = [
    "Register your application in Azure Portal",
    "Add the redirect URL shown above to 'Redirect URIs'",
    "Configure required API permissions for Microsoft Graph",
    "Copy Client ID and Client Secret",
    "Configure credentials in Tracecat with your tenant ID",
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-600 hover:text-slate-900"
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back
            </Button>
            <div className="text-sm text-slate-500">
              Integrations <span className="mx-2">›</span> Microsoft
            </div>
          </div>
        </div>

        {/* Main Header Card */}
        <Card className="border-0 shadow-lg bg-white">
          <CardHeader className="pb-4">
            <div className="flex items-start justify-between">
              <div className="flex items-center space-x-4">
                <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl flex items-center justify-center shadow-lg">
                  <div className="w-6 h-6 bg-white rounded-sm flex items-center justify-center">
                    <div className="w-3 h-3 bg-gradient-to-br from-red-500 via-green-500 to-blue-500 rounded-sm"></div>
                  </div>
                </div>
                <div>
                  <CardTitle className="text-2xl font-bold text-slate-900">
                    Microsoft
                  </CardTitle>
                  <CardDescription className="text-slate-600 mt-1">
                    Microsoft OAuth provider
                  </CardDescription>
                </div>
              </div>
              <div className="flex items-center space-x-2">
                <Badge
                  variant="secondary"
                  className="bg-green-50 text-green-700 border-green-200"
                >
                  auth
                </Badge>
                <Badge
                  variant="default"
                  className="bg-green-600 hover:bg-green-700"
                >
                  <CheckCircle className="w-3 h-3 mr-1" />
                  Connected
                </Badge>
              </div>
            </div>
          </CardHeader>
        </Card>

        {/* Tabs */}
        <Tabs defaultValue="overview" className="space-y-6">
          <TabsList className="bg-white border shadow-sm">
            <TabsTrigger
              value="overview"
              className="data-[state=active]:bg-slate-100"
            >
              Overview
            </TabsTrigger>
            <TabsTrigger
              value="configuration"
              className="data-[state=active]:bg-slate-100"
            >
              Configuration
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-6">
            <div className="grid lg:grid-cols-2 gap-6">
              {/* Connection Status */}
              <Card className="shadow-lg border-0">
                <CardHeader>
                  <CardTitle className="flex items-center space-x-2">
                    <Zap className="w-5 h-5 text-green-600" />
                    <span>Connection Status</span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="flex items-center space-x-3">
                    <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
                    <span className="font-medium text-green-700">
                      Connected
                    </span>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <label className="text-sm font-medium text-slate-700">
                        Token Type
                      </label>
                      <p className="text-slate-600">Bearer</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-slate-700">
                        Expires
                      </label>
                      <p className="text-slate-600">6/29/2025</p>
                    </div>
                  </div>

                  <Separator />

                  <div className="space-y-3">
                    <h4 className="font-medium text-slate-900">
                      Requested Scopes
                    </h4>
                    <div className="flex flex-wrap gap-2">
                      {requestedScopes.map((scope) => (
                        <Badge
                          key={scope}
                          variant="outline"
                          className="text-xs"
                        >
                          {scope}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h4 className="font-medium text-slate-900">
                      Granted Scopes
                    </h4>
                    <div className="flex flex-wrap gap-2 max-h-32 overflow-y-auto">
                      {grantedScopes.map((scope) => (
                        <Badge
                          key={scope}
                          variant="secondary"
                          className="text-xs bg-blue-50 text-blue-700 border-blue-200"
                        >
                          {scope}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <Button variant="destructive" className="w-full mt-6">
                    Disconnect
                  </Button>
                </CardContent>
              </Card>

              {/* Features & Documentation */}
              <div className="space-y-6">
                <Card className="shadow-lg border-0">
                  <CardHeader>
                    <CardTitle className="flex items-center space-x-2">
                      <Shield className="w-5 h-5 text-blue-600" />
                      <span>Features</span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {features.map((feature) => (
                        <div
                          key={feature.name}
                          className="flex items-center space-x-3"
                        >
                          <CheckCircle className="w-4 h-4 text-green-600" />
                          <span className="text-slate-700">{feature.name}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className="shadow-lg border-0">
                  <CardHeader>
                    <CardTitle>Documentation</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Button
                      variant="outline"
                      className="w-full justify-between bg-transparent"
                      asChild
                    >
                      <a href="#" className="flex items-center">
                        API Documentation
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    </Button>
                    <Button
                      variant="outline"
                      className="w-full justify-between bg-transparent"
                      asChild
                    >
                      <a href="#" className="flex items-center">
                        Setup Guide
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    </Button>
                    <Button
                      variant="outline"
                      className="w-full justify-between bg-transparent"
                      asChild
                    >
                      <a href="#" className="flex items-center">
                        Troubleshooting
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    </Button>
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* Setup Guide */}
            <Card className="shadow-lg border-0">
              <CardHeader>
                <CardTitle>Setup Guide</CardTitle>
                <CardDescription>
                  Follow these steps to complete the integration
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {setupSteps.map((step, index) => (
                    <div key={index} className="flex items-start space-x-3">
                      <div className="w-6 h-6 bg-green-100 rounded-full flex items-center justify-center mt-0.5">
                        <CheckCircle className="w-4 h-4 text-green-600" />
                      </div>
                      <div className="flex-1">
                        <p className="text-slate-700 line-through opacity-60">
                          {step}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="configuration">
            <Card className="shadow-lg border-0">
              <CardHeader>
                <CardTitle>Configuration Settings</CardTitle>
                <CardDescription>
                  Manage your Microsoft integration configuration
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-slate-600">
                  Configuration options will be displayed here.
                </p>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
