import type {NextConfig} from 'next';
const config:NextConfig={
  devIndicators:false,
  skipTrailingSlashRedirect:true,
  agentRules:false,
  allowedDevOrigins:process.env.PRESIDIUM_PUBLIC_ORIGIN?[new URL(process.env.PRESIDIUM_PUBLIC_ORIGIN).hostname]:[],
  async rewrites(){const target=process.env.PRESIDIUM_API_PROXY_URL||'http://127.0.0.1:8000';return [{source:'/v1/:path*',destination:`${target}/v1/:path*`}];}
};
export default config;
